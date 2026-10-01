import time
import numpy as np

from PySide6.QtCore import QObject, QThread, Signal

from Spectro_diode.avaspec import (
    AVS_Init,
    AVS_Done,
    AVS_GetNrOfDevices,
    AVS_GetList,
    AVS_Activate,
    AVS_Deactivate,
    AVS_GetLambda,
    AVS_GetParameter,
    AVS_GetVersionInfo,
    AVS_UseHighResAdc,
    AVS_PrepareMeasure,
    AVS_Measure,
    AVS_PollScan,
    AVS_GetScopeData,
    AVS_StopMeasure,
    INVALID_AVS_HANDLE_VALUE,
    MeasConfigType,
)


class LiveSpectrumThread(QThread):
    """
    Worker thread responsible only for continuous spectrum acquisition.

    The GUI never communicates directly with the Avantes SDK while the
    acquisition loop is running.

    Signals
    -------
    spectrum_ready:
        Emits (wavelengths, intensities) for every acquired spectrum.

    status_update:
        Human-readable status messages.

    error:
        Error messages generated during acquisition.

    finished:
        Emitted when the acquisition loop terminates.
    """

    spectrum_ready = Signal(object, object)
    status_update = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        handle,
        wavelengths,
        integration_time=10.0,
        start_pixel=0,
        stop_pixel=None,
        parent=None,
    ):
        super().__init__(parent)

        self.handle = handle
        self.wavelengths = np.asarray(wavelengths)

        self.integration_time = float(integration_time)
        self.start_pixel = int(start_pixel)

        if stop_pixel is None:
            stop_pixel = len(self.wavelengths) - 1

        self.stop_pixel = int(stop_pixel)

        self.running = False

    def stop(self):
        """Request the acquisition loop to stop."""

        self.running = False

        # Stop the current measurement if possible.
        if self.handle is not None:
            try:
                AVS_StopMeasure(self.handle)
            except Exception:
                pass

    def run(self):
        """Continuous software-triggered acquisition."""

        self.running = True

        try:
            # Make sure the spectrometer starts from a clean state.
            try:
                AVS_StopMeasure(self.handle)
                time.sleep(0.1)
            except Exception:
                pass

            # ----------------------------------------------------------
            # Configure measurement
            # ----------------------------------------------------------

            AVS_UseHighResAdc(self.handle, True)

            config = MeasConfigType()

            config.m_StartPixel = self.start_pixel
            config.m_StopPixel = self.stop_pixel

            config.m_IntegrationTime = self.integration_time
            config.m_IntegrationDelay = 0
            config.m_NrAverages = 1

            # Disable corrections/smoothing for live display.
            config.m_CorDynDark_m_Enable = 0
            config.m_CorDynDark_m_ForgetPercentage = 0

            config.m_Smoothing_m_SmoothPix = 0
            config.m_Smoothing_m_SmoothModel = 0

            config.m_SaturationDetection = 1

            # Software trigger.
            config.m_Trigger_m_Mode = 0
            config.m_Trigger_m_Source = 0
            config.m_Trigger_m_SourceType = 0

            # No laser control from this class.
            config.m_Control_m_StrobeControl = 0
            config.m_Control_m_LaserDelay = 0
            config.m_Control_m_LaserWidth = 0
            config.m_Control_m_LaserWaveLength = 0.0

            # Do not store scans in spectrometer RAM.
            config.m_Control_m_StoreToRam = 0

            ret = AVS_PrepareMeasure(self.handle, config)

            if ret != 0:
                self.error.emit(
                    f"Failed to prepare live measurement. Error code: {ret}"
                )
                return

            self.status_update.emit("Live spectrum acquisition started")

            # ----------------------------------------------------------
            # Continuous acquisition
            # ----------------------------------------------------------

            wavelength_array = self.wavelengths[
                self.start_pixel:self.stop_pixel + 1
            ].copy()

            while self.running:

                # Start one measurement.
                ret = AVS_Measure(
                    self.handle,
                    0,
                    1,
                )

                if ret != 0:
                    if self.running:
                        self.error.emit(
                            f"Failed to start measurement. Error code: {ret}"
                        )
                    break

                # ------------------------------------------------------
                # Wait for measurement to finish
                # ------------------------------------------------------

                ready = False

                # Give enough time for the integration plus overhead.
                timeout = max(
                    1.0,
                    self.integration_time / 1000.0 + 1.0,
                )

                start_time = time.perf_counter()

                while self.running:

                    try:
                        if AVS_PollScan(self.handle):
                            ready = True
                            break

                    except Exception as exc:
                        self.error.emit(
                            f"Error polling spectrometer: {exc}"
                        )
                        self.running = False
                        break

                    if time.perf_counter() - start_time > timeout:
                        self.error.emit(
                            "Timeout waiting for spectrum"
                        )
                        break

                    # Polling interval.
                    time.sleep(0.002)

                if not self.running:
                    break

                if not ready:
                    continue

                # ------------------------------------------------------
                # Retrieve spectrum
                # ------------------------------------------------------

                try:
                    timestamp, spectrum = AVS_GetScopeData(self.handle)

                    # AVS_GetScopeData returns the complete detector array.
                    spectrum_array = np.asarray(
                        spectrum[
                            self.start_pixel:self.stop_pixel + 1
                        ],
                        dtype=np.float64,
                    )

                    # Emit the spectrum to the Qt event system.
                    #
                    # The GUI receives this signal in its own thread.
                    self.spectrum_ready.emit(
                        wavelength_array,
                        spectrum_array,
                    )

                except Exception as exc:

                    if self.running:
                        self.error.emit(
                            f"Error retrieving spectrum: {exc}"
                        )

            self.status_update.emit("Live spectrum acquisition stopped")

        except Exception as exc:

            self.error.emit(
                f"Live spectrum error: {exc}"
            )

        finally:

            try:
                AVS_StopMeasure(self.handle)
            except Exception:
                pass

            self.running = False
            self.finished.emit()


class Spectrometer(QObject):
    """
    High-level Avantes spectrometer interface.

    This class contains no GUI code.

    Responsibilities
    ----------------
    - Initialize the Avantes SDK
    - Find and activate the spectrometer
    - Obtain detector/wavelength information
    - Start/stop live spectrum acquisition
    - Provide spectrum analysis functions
    - Emit Qt signals for the main application

    The main window can therefore use this class without knowing
    anything about the Avantes SDK.
    """

    # --------------------------------------------------------------
    # Qt signals
    # --------------------------------------------------------------

    spectrum_ready = Signal(object, object)
    """
    Emitted whenever a new spectrum is available.

    Parameters
    ----------
    wavelengths : numpy.ndarray
        Wavelength values in nm.

    intensities : numpy.ndarray
        Measured intensity/count values.
    """

    status_update = Signal(str)
    error = Signal(str)

    connected = Signal()
    disconnected = Signal()

    # --------------------------------------------------------------
    # Initialization
    # --------------------------------------------------------------

    def __init__(self, parent=None):
        super().__init__(parent)

        self.handle = None
        self.serial = None
        self.num_pixels = 0

        self.wavelengths = None
        self.adc_max = 65535

        self.live_thread = None

        self._connected = False

    # --------------------------------------------------------------
    # Connection
    # --------------------------------------------------------------

    def connect(self):
        """
        Initialize the Avantes SDK and connect to the first
        available spectrometer.

        Returns
        -------
        bool
            True if connection succeeded.
        """

        if self._connected:
            return True

        try:
            # Initialize Avantes library.
            ret = AVS_Init(0)

            if ret < 0:
                self.error.emit(
                    f"Failed to initialize Avantes library: {ret}"
                )
                return False

            self.status_update.emit(
                f"Avantes library initialized. Found {ret} device(s)."
            )

            # Check for connected devices.
            num_devices = AVS_GetNrOfDevices()

            if num_devices <= 0:
                self.error.emit(
                    "No Avantes spectrometers found."
                )
                AVS_Done()
                return False

            # Obtain device list.
            devices = AVS_GetList(num_devices)

            device = devices[0]

            # Serial number.
            try:
                self.serial = device.SerialNumber.decode("utf-8")
            except AttributeError:
                self.serial = str(device.SerialNumber)

            self.status_update.emit(
                f"Connecting to spectrometer: {self.serial}"
            )

            # Activate device.
            self.handle = AVS_Activate(device)

            if self.handle == INVALID_AVS_HANDLE_VALUE:
                self.error.emit(
                    "Failed to activate spectrometer."
                )

                AVS_Done()
                self.handle = None

                return False

            # ----------------------------------------------------------
            # Spectrometer configuration
            # ----------------------------------------------------------

            # Use 16-bit ADC mode.
            AVS_UseHighResAdc(
                self.handle,
                True,
            )

            # Detector configuration.
            device_config = AVS_GetParameter(
                self.handle,
                63484,
            )

            self.num_pixels = (
                device_config.m_Detector_m_NrPixels
            )

            # Wavelength calibration.
            self.wavelengths = np.asarray(
                AVS_GetLambda(self.handle),
                dtype=np.float64,
            )

            # ADC maximum for 16-bit mode.
            self.adc_max = 65535

            self._connected = True

            self.status_update.emit(
                f"Spectrometer connected: "
                f"{self.num_pixels} pixels, "
                f"{self.wavelengths[0]:.2f}–"
                f"{self.wavelengths[-1]:.2f} nm"
            )

            self.connected.emit()

            return True

        except Exception as exc:

            self.error.emit(
                f"Spectrometer connection error: {exc}"
            )

            self.handle = None
            self._connected = False

            try:
                AVS_Done()
            except Exception:
                pass

            return False

    # --------------------------------------------------------------
    # Properties
    # --------------------------------------------------------------

    @property
    def is_connected(self):
        return self._connected

    # --------------------------------------------------------------
    # Live spectrum
    # --------------------------------------------------------------

    def start_live(
        self,
        integration_time=10.0,
        start_pixel=0,
        stop_pixel=None,
    ):
        """
        Start continuous live spectrum acquisition.

        Parameters
        ----------
        integration_time : float
            Integration time in milliseconds.

        start_pixel : int
            First detector pixel.

        stop_pixel : int or None
            Last detector pixel.
        """

        if not self._connected or self.handle is None:
            self.error.emit(
                "Cannot start live spectrum: "
                "spectrometer is not connected."
            )
            return False

        if self.live_thread is not None:
            if self.live_thread.isRunning():
                return False

        if stop_pixel is None:
            stop_pixel = self.num_pixels - 1

        # Validate pixel range.
        start_pixel = max(
            0,
            min(start_pixel, self.num_pixels - 1),
        )

        stop_pixel = max(
            0,
            min(stop_pixel, self.num_pixels - 1),
        )

        if start_pixel >= stop_pixel:
            self.error.emit(
                "Invalid pixel range."
            )
            return False

        # Create acquisition thread.
        self.live_thread = LiveSpectrumThread(
            handle=self.handle,
            wavelengths=self.wavelengths,
            integration_time=integration_time,
            start_pixel=start_pixel,
            stop_pixel=stop_pixel,
        )

        # Forward worker signals through this class.
        self.live_thread.spectrum_ready.connect(
            self._on_spectrum_ready
        )

        self.live_thread.status_update.connect(
            self.status_update
        )

        self.live_thread.error.connect(
            self.error
        )

        self.live_thread.finished.connect(
            self._on_live_finished
        )

        self.live_thread.start()

        return True

    def stop_live(self):
        """Stop live spectrum acquisition."""

        if self.live_thread is None:
            return

        if self.live_thread.isRunning():
            self.status_update.emit(
                "Stopping live spectrum..."
            )

            self.live_thread.stop()

            # Wait for the acquisition thread to finish.
            self.live_thread.wait()

        self.live_thread = None

    def _on_spectrum_ready(
        self,
        wavelengths,
        intensities,
    ):
        """
        Forward the worker spectrum signal.

        This is intentionally kept as a separate method so that
        spectrum processing can be inserted here later if needed.
        """

        self.spectrum_ready.emit(
            wavelengths,
            intensities,
        )

    def _on_live_finished(self):
        self.status_update.emit(
            "Live spectrum thread finished."
        )

    def is_live_running(self):
        """Check if the live acquisition thread is running."""

        if self.live_thread is None:
            return False

        return self.live_thread.isRunning()

    # --------------------------------------------------------------
    # Spectrum analysis
    # --------------------------------------------------------------

    @staticmethod
    def calculate_statistics(
        wavelengths,
        intensities,
        adc_max=65535,
    ):
        """
        Calculate basic statistics for a spectrum.

        Returns
        -------
        dict
        """

        wavelengths = np.asarray(wavelengths)
        intensities = np.asarray(intensities)

        max_index = np.argmax(intensities)
        maximum = np.max(intensities)

        return {
            "max": maximum,
            "min": np.min(intensities),
            "mean": np.mean(intensities),
            "std": np.std(intensities),
            "max_wavelength": wavelengths[max_index],
            "saturation": (
                maximum / adc_max
            ) * 100.0,
        }

    # --------------------------------------------------------------
    # Disconnect
    # --------------------------------------------------------------

    def disconnect(self):
        """Stop acquisition and disconnect the spectrometer."""

        # Stop live acquisition first.
        self.stop_live()

        if self.handle is not None:

            try:
                AVS_StopMeasure(self.handle)
            except Exception:
                pass

            try:
                AVS_Deactivate(self.handle)
            except Exception:
                pass

            self.handle = None

        try:
            AVS_Done()
        except Exception:
            pass

        self._connected = False

        self.status_update.emit(
            "Spectrometer disconnected."
        )

        self.disconnected.emit()

    # --------------------------------------------------------------
    # Cleanup
    # --------------------------------------------------------------

    def close(self):
        """Alias for disconnect()."""

        self.disconnect()

