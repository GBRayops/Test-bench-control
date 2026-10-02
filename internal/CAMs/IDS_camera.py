"""
camera.py
----------

Production-oriented IDS uEye camera wrapper.

Requires:
    pyueye
    numpy
    threading

Author: ChatGPT
"""

from pyueye import ueye
import threading


class CameraError(RuntimeError):
    pass


class IDSCamera:

    ####################################################################
    # Constructor
    ####################################################################

    def __init__(self, camera_id=0):

        self.camera_id = camera_id
        self.hCam = ueye.HIDS(camera_id)

        self.width = 0
        self.height = 0
        self.bits_per_pixel = 8
        self.bytes_per_pixel = 1

        self.color_mode = ueye.IS_CM_MONO8

        self.mem_ptr = ueye.c_mem_p()
        self.mem_id = ueye.int()

        self.pitch = ueye.int()

        self.initialized = False
        self.running = False

        self.lock = threading.Lock()

    ####################################################################
    # Utility
    ####################################################################

    def _check(self, result, message):
        if result != ueye.IS_SUCCESS:
            raise CameraError(f"{message} (error {result})")

    ####################################################################
    # Initialization
    ####################################################################
    def open(self):
        if self.initialized:
            return

        self._check(
            ueye.is_InitCamera(self.hCam, None),
            "InitCamera"
        )

        self._load_sensor_information()

        self._configure_color_mode()

        self._allocate_memory()

        self.initialized = True
    ####################################################################
    # Sensor Information
    ####################################################################

    def _load_sensor_information(self):

        sensor = ueye.SENSORINFO()

        self._check(
            ueye.is_GetSensorInfo(self.hCam, sensor),
            "Cannot read sensor information"
        )

        rect = ueye.IS_RECT()

        self._check(
            ueye.is_AOI(
                self.hCam,
                ueye.IS_AOI_IMAGE_GET_AOI,
                rect,
                ueye.sizeof(rect)
            ),
            "Cannot read AOI"
        )

        self.width = rect.s32Width.value
        self.height = rect.s32Height.value

    ####################################################################
    # Color
    ####################################################################

    def _configure_color_mode(self):

        self.color_mode = ueye.IS_CM_MONO8

        self.bits_per_pixel = 8
        self.bytes_per_pixel = 1

        self._check(
            ueye.is_SetColorMode(
                self.hCam,
                self.color_mode
            ),
            "Cannot set color mode"
        )

    ####################################################################
    # Memory
    ####################################################################

    def _allocate_memory(self):

        self.mem_ptr = ueye.c_mem_p()
        self.mem_id = ueye.int()
        self.pitch = ueye.int()

        self._check(
            ueye.is_AllocImageMem(
                self.hCam,
                self.width,
                self.height,
                self.bits_per_pixel,
                self.mem_ptr,
                self.mem_id
            ),
            "Unable to allocate image memory"
        )

        self._check(
            ueye.is_SetImageMem(
                self.hCam,
                self.mem_ptr,
                self.mem_id
            ),
            "Unable to activate image memory"
        )

        x = ueye.int()
        y = ueye.int()
        bits = ueye.int()
        self.pitch = ueye.int()

        self._check(
            ueye.is_InquireImageMem(
                    self.hCam,
                    self.mem_ptr,
                    self.mem_id,
                    x,
                    y,
                    bits,
                    self.pitch
                ),
                "Unable to inquire image memory"
            )

    def start(self):

        if self.running:
            return

        live = ueye.is_CaptureVideo(
            self.hCam,
            ueye.IS_GET_LIVE
        )

        if live == 1:
            print("Driver reports capture already running.")
            ueye.is_StopLiveVideo(
                self.hCam,
                ueye.IS_FORCE_VIDEO_STOP
            )

        ret = ueye.is_CaptureVideo(
            self.hCam,
            ueye.IS_DONT_WAIT
        )

        self._check(ret, "Unable to start acquisition")

        self.running = True

        ####################################################################
    def stop(self):

        if not self.running:
            return

        #
        # Tell every thread we're stopping
        #

        self.running = False

        self._check(
            ueye.is_StopLiveVideo(
                self.hCam,
                ueye.IS_FORCE_VIDEO_STOP
            ),
            "Unable to stop acquisition"
        )

###########################################################################
# Get Frame
###########################################################################

    def get_frame(self):

        if not self.running:
            return None

        with self.lock:

            img = ueye.get_data(
                self.mem_ptr,
                self.width,
                self.height,
                self.bits_per_pixel,
                self.pitch,
                copy=True
            )

        return img.reshape(
            self.height,
            self.width
    )
    ####################################################################
    # Exposure
    ####################################################################

    def get_exposure(self):

        with self.lock:

            exposure = ueye.DOUBLE()

            self._check(
                ueye.is_Exposure(
                    self.hCam,
                    ueye.IS_EXPOSURE_CMD_GET_EXPOSURE,
                    exposure,
                    ueye.sizeof(exposure)
                ),
                "Cannot read exposure"
            )

            return exposure.value

    ####################################################################

    def set_exposure(self, milliseconds):

        with self.lock:

            exposure = ueye.DOUBLE(float(milliseconds))

            self._check(
                ueye.is_Exposure(
                    self.hCam,
                    ueye.IS_EXPOSURE_CMD_SET_EXPOSURE,
                    exposure,
                    ueye.sizeof(exposure)
                ),
                "Cannot set exposure"
            )

    ####################################################################
    # Gain
    ####################################################################

    def get_gain(self):

        with self.lock:

            return ueye.is_SetHardwareGain(
                self.hCam,
                ueye.IS_GET_MASTER_GAIN,
                ueye.IS_IGNORE_PARAMETER,
                ueye.IS_IGNORE_PARAMETER,
                ueye.IS_IGNORE_PARAMETER
            )

    ####################################################################

    def set_gain(self, gain):

        gain = max(0, min(100, int(gain)))

        with self.lock:

            self._check(
                ueye.is_SetHardwareGain(
                    self.hCam,
                    gain,
                    ueye.IS_IGNORE_PARAMETER,
                    ueye.IS_IGNORE_PARAMETER,
                    ueye.IS_IGNORE_PARAMETER
                ),
                "Cannot set gain"
            )

    ####################################################################
    # Frame Rate
    ####################################################################

    def get_framerate(self):

        fps = ueye.DOUBLE()

        self._check(
            ueye.is_GetFramesPerSecond(
                self.hCam,
                fps
            ),
            "Cannot get framerate"
        )

        return fps.value

    ####################################################################
    def set_framerate(self, fps):

        with self.lock:

            actual = ueye.DOUBLE()
            requested = ueye.DOUBLE(float(fps))

            self._check(
                ueye.is_SetFrameRate(
                    self.hCam,
                    requested,
                    actual
                ),
                "Cannot set framerate"
            )

            return actual.value

    ####################################################################
    # Pixel Clock
    ####################################################################

    def get_pixel_clock(self):

        value = ueye.uint()

        self._check(
            ueye.is_PixelClock(
                self.hCam,
                ueye.IS_PIXELCLOCK_CMD_GET,
                value,
                ueye.sizeof(value)
            ),
            "Cannot read pixel clock"
        )

        return value.value

    ####################################################################

    def set_pixel_clock(self, mhz):
        with self.lock:
            value = ueye.uint(int(mhz))

            self._check(
                ueye.is_PixelClock(
                    self.hCam,
                    ueye.IS_PIXELCLOCK_CMD_SET,
                    value,
                    ueye.sizeof(value)
                ),
                "Cannot set pixel clock"
            )

    ####################################################################
    # ROI
    ####################################################################

    def get_roi(self):

        rect = ueye.IS_RECT()

        self._check(
            ueye.is_AOI(
                self.hCam,
                ueye.IS_AOI_IMAGE_GET_AOI,
                rect,
                ueye.sizeof(rect)
            ),
            "Cannot read ROI"
        )

        return (
            rect.s32X.value,
            rect.s32Y.value,
            rect.s32Width.value,
            rect.s32Height.value
        )

    ####################################################################

    def set_roi(self, x, y, width, height):

        self.stop()

        ueye.is_FreeImageMem(
            self.hCam,
            self.mem_ptr,
            self.mem_id
        )

        rect = ueye.IS_RECT()

        rect.s32X = ueye.int(x)
        rect.s32Y = ueye.int(y)
        rect.s32Width = ueye.int(width)
        rect.s32Height = ueye.int(height)

        self._check(
            ueye.is_AOI(
                self.hCam,
                ueye.IS_AOI_IMAGE_SET_AOI,
                rect,
                ueye.sizeof(rect)
            ),
            "Cannot set ROI"
        )

        self.width = width
        self.height = height

        self.mem_ptr = ueye.c_mem_p()
        self.mem_id = ueye.int()

        self._allocate_memory()

        self.start()

    ####################################################################
    # Trigger
    ####################################################################

    def set_free_run(self):

        self._check(
            ueye.is_SetExternalTrigger(
                self.hCam,
                ueye.IS_SET_TRIGGER_OFF
            ),
            "Cannot disable trigger"
        )

    ####################################################################

    def set_hardware_trigger(self):

        self._check(
            ueye.is_SetExternalTrigger(
                self.hCam,
                ueye.IS_SET_TRIGGER_HI_LO
            ),
            "Cannot enable hardware trigger"
        )

    ####################################################################

    def software_trigger(self):

        self._check(
            ueye.is_ForceTrigger(self.hCam),
            "Software trigger failed"
        )

    ####################################################################
    # Camera Information
    ####################################################################

    def get_camera_info(self):

        info = ueye.CAMINFO()

        self._check(
            ueye.is_GetCameraInfo(
                self.hCam,
                info
            ),
            "Cannot get camera info"
        )

        return {
            "serial": info.SerNo.decode(errors="ignore").strip(),
            "id": info.ID.decode(errors="ignore").strip(),
            "version": info.Version.decode(errors="ignore").strip(),
            "date": info.Date.decode(errors="ignore").strip(),
        }

    ####################################################################
    # Cleanup
    ####################################################################

    def close(self):

        if not self.initialized:
            return

        if self.running:
            self.stop()

        with self.lock:

            if self.mem_ptr:
                self._check(
                    ueye.is_FreeImageMem(
                        self.hCam,
                        self.mem_ptr,
                        self.mem_id
                    ),
                    "Unable to free image memory"
                )

                self.mem_ptr = ueye.c_mem_p()
                self.mem_id = ueye.int()

            self._check(
                ueye.is_ExitCamera(self.hCam),
                "Unable to close camera"
            )

        self.initialized = False
        print("ExitCamera returned successfully")
    ###############################################################################
    # Auto Exposure
    ###############################################################################

    def enable_auto_exposure(self, enabled=False):

        value = ueye.DOUBLE(1.0 if enabled else 0.0)

        self._check(
            ueye.is_SetAutoParameter(
                self.hCam,
                ueye.IS_SET_ENABLE_AUTO_SHUTTER,
                value,
                None
            ),
            "Unable to change auto exposure"
        )


    ###############################################################################
    # Auto Gain
    ###############################################################################

    def enable_auto_gain(self, enabled=False):

        value = ueye.DOUBLE(1.0 if enabled else 0.0)

        self._check(
            ueye.is_SetAutoParameter(
                self.hCam,
                ueye.IS_SET_ENABLE_AUTO_GAIN,
                value,
                None
            ),
            "Unable to change auto gain"
        )


    ###############################################################################
    # Auto White Balance
    ###############################################################################

    def enable_auto_white_balance(self, enabled=False):

        value = ueye.DOUBLE(1.0 if enabled else 0.0)

        self._check(
            ueye.is_SetAutoParameter(
                self.hCam,
                ueye.IS_SET_ENABLE_AUTO_WHITEBALANCE,
                value,
                None
            ),
            "Unable to change auto white balance"
        )


    ###############################################################################
    # Gamma
    ###############################################################################

    def get_gamma(self):

        gamma = ueye.int()

        self._check(
            ueye.is_Gamma(
                self.hCam,
                ueye.IS_GAMMA_CMD_GET,
                gamma,
                ueye.sizeof(gamma)
            ),
            "Unable to read gamma"
        )

        return gamma.value


    def set_gamma(self, value):

        gamma = ueye.int(int(value))

        self._check(
            ueye.is_Gamma(
                self.hCam,
                ueye.IS_GAMMA_CMD_SET,
                gamma,
                ueye.sizeof(gamma)
            ),
            "Unable to set gamma"
        )


    ###############################################################################
    # Black Level
    ###############################################################################

    def get_black_level(self):

        level = ueye.int()

        self._check(
            ueye.is_Blacklevel(
                self.hCam,
                ueye.IS_BLACKLEVEL_CMD_GET_OFFSET,
                level,
                ueye.sizeof(level)
            ),
            "Unable to read black level"
        )

        return level.value


    def set_black_level(self, value):

        level = ueye.int(int(value))

        self._check(
            ueye.is_Blacklevel(
                self.hCam,
                ueye.IS_BLACKLEVEL_CMD_SET_OFFSET,
                level,
                ueye.sizeof(level)
            ),
            "Unable to set black level"
        )


    ###############################################################################
    # Software Freeze
    ###############################################################################

    def freeze(self):

        self._check(
            ueye.is_FreezeVideo(
                self.hCam,
                ueye.IS_WAIT
            ),
            "Freeze failed"
        )


    ###############################################################################
    # Image Save
    ###############################################################################


    ###############################################################################
    # Camera Parameters
    ###############################################################################

    def save_parameters(self, filename):

        self._check(
            ueye.is_ParameterSet(
                self.hCam,
                ueye.IS_PARAMETERSET_CMD_SAVE_FILE,
                filename,
                0
            ),
            "Unable to save parameter set"
        )


    def load_parameters(self, filename):

        self._check(
            ueye.is_ParameterSet(
                self.hCam,
                ueye.IS_PARAMETERSET_CMD_LOAD_FILE,
                filename,
                0
            ),
            "Unable to load parameter set"
        )


    ###############################################################################
    # Memory Information
    ###############################################################################

    def get_image_size(self):

        return self.width, self.height


    def get_pitch(self):

        return self.pitch.value


    ###############################################################################
    # Camera Status
    ###############################################################################

    def is_open(self):

        return self.initialized


    def is_running(self):

        return self.running


    ###############################################################################
    # Camera Temperature (if supported)
    ###############################################################################

    def get_temperature(self):

        temp = ueye.int()

        result = ueye.is_DeviceFeature(
            self.hCam,
            ueye.IS_DEVICE_FEATURE_CMD_GET_TEMPERATURE,
            temp,
            ueye.sizeof(temp)
        )
        if result != ueye.IS_SUCCESS:
            
            return 
        

        return temp.value


    ###############################################################################
    # Reset Camera
    ###############################################################################

    def reset_defaults(self):

        self._check(
            ueye.is_ResetToDefault(self.hCam),
            "Unable to restore defaults"
        )


    ###############################################################################
    # Context Manager
    ###############################################################################

    def __enter__(self):

        self.open()
        self.start()

        return self


    def __exit__(self, exc_type, exc, tb):

        self.close()


    ###############################################################################
    # String Representation
    ###############################################################################

    def __repr__(self):

        if not self.initialized:
            return "<IDSCamera Closed>"

        return (
            f"<IDSCamera "
            f"{self.width}x{self.height} "
            f"FPS={self.get_framerate():.2f} "
            f"Exposure={self.get_exposure():.2f} ms>"
        )


    ###############################################################################
    # Camera Enumeration
    ###############################################################################

    @staticmethod
    def list_cameras():

        number = ueye.int()

        result = ueye.is_GetNumberOfCameras(number)

        if result != ueye.IS_SUCCESS:
            return []

        return list(range(number.value))