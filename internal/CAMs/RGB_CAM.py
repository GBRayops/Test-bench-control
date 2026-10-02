import cv2
import numpy as np
import threading
import time

from PySide6.QtCore import (Signal, Slot, QObject)


class USBCameraError(Exception):
    """Exception raised for USB camera errors."""
    pass


class USBCamera:

    def __init__(self, camera_id = 0):

        self.camera_id = camera_id

        self.cap = None

        self.width = 640
        self.height = 480
        self.fps = 30.0

        self.initialized = False
        self.running = False

        self.lock = threading.Lock()

    ####################################################################
    # Open
    ####################################################################
    def set_device_index(self, device_index):

        self.device_index = int(device_index)

    def open(self):

        if self.initialized:
            return

        self.cap = cv2.VideoCapture(
            self.device_index,
            cv2.CAP_DSHOW
        )
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0) #DISABLE AUTO EXPOSURE
        if not self.cap.isOpened():

            self.cap.release()
            self.cap = None

            raise USBCameraError(
                f"Unable to open USB camera {self.camera_id}"
            )

        self.cap.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            self.width
        )

        self.cap.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            self.height
        )

        #
        # Apply requested FPS
        #

        self.cap.set(
            cv2.CAP_PROP_FPS,
            self.fps
        )

        #
        # Read back the actual values accepted
        # by the camera/driver.
        #

        self.width = int(
            self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        )

        self.height = int(
            self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        )

        actual_fps = self.cap.get(
            cv2.CAP_PROP_FPS
        )

        if actual_fps > 0:
            self.fps = actual_fps
        
        fourcc = cv2.VideoWriter_fourcc(*'MJPG')

        if not self.cap.set(
            cv2.CAP_PROP_FOURCC,
            fourcc):
            print(
                "Warning: unable to request MJPG"
            )
        self.initialized = True

    ####################################################################
    # Start
    ####################################################################

    def start(self):

        if not self.initialized:
            raise USBCameraError(
                "Camera is not initialized"
            )

        if self.running:
            return

        self.running = True

    ####################################################################
    # Stop
    ####################################################################

    def stop(self):

        if not self.running:
            return

        self.running = False

    ####################################################################
    # Close
    ####################################################################

    def close(self):

        if not self.initialized:
            return

        #
        # Stop acquisition first
        #

        self.stop()

        #
        # Release OpenCV camera
        #

        with self.lock:

            if self.cap is not None:

                self.cap.release()

                self.cap = None

        self.initialized = False

    ####################################################################
    # Get Frame
    ####################################################################

    def get_frame(self):

        if not self.initialized:
            return None

        if not self.running:
            return None

        with self.lock:

            if self.cap is None:
                return None

            ret, frame = self.cap.read()

        if not ret:
            raise USBCameraError(
                "Failed to read frame from USB camera"
            )

        return frame

    ####################################################################
    # FPS
    ####################################################################

    def get_framerate(self):

        if not self.initialized or self.cap is None:
            return self.fps

        with self.lock:

            fps = self.cap.get(
                cv2.CAP_PROP_FPS
            )

        if fps > 0:
            self.fps = fps

        return self.fps

    ####################################################################

    def set_framerate(self, fps):

        fps = float(fps)

        if fps <= 0:
            raise ValueError(
                "FPS must be greater than zero"
            )

        if not self.initialized or self.cap is None:
            self.fps = fps
            return self.fps

        with self.lock:

            success = self.cap.set(
                cv2.CAP_PROP_FPS,
                fps
            )

        if not success:
            raise USBCameraError(
                "Unable to set USB camera FPS"
            )

        actual = self.cap.get(
            cv2.CAP_PROP_FPS
        )

        if actual > 0:
            self.fps = actual


        #force MJPEG after changes in FPS
        mjpg = cv2.VideoWriter_fourcc(*"MJPG")

        self.cap.set(
            cv2.CAP_PROP_FOURCC,
            mjpg
        )

        return self.fps

    ####################################################################
    # Resolution
    ####################################################################

    def get_resolution(self):

        if not self.initialized or self.cap is None:
            return self.width, self.height

        with self.lock:

            width = int(
                self.cap.get(
                    cv2.CAP_PROP_FRAME_WIDTH
                )
            )

            height = int(
                self.cap.get(
                    cv2.CAP_PROP_FRAME_HEIGHT
                )
            )

        if width > 0:
            self.width = width

        if height > 0:
            self.height = height

        return self.width, self.height

    ####################################################################

    def set_resolution(self, width, height):

            if not self.initialized:
                raise RuntimeError(
                    "USB camera is not initialized"
                )

            # 1. Request resolution
            self.cap.set(
                cv2.CAP_PROP_FRAME_WIDTH,
                int(width)
            )

            self.cap.set(
                cv2.CAP_PROP_FRAME_HEIGHT,
                int(height)
            )

            # 2. IMPORTANT:
            # Request MJPG AFTER resolution.
            mjpg = cv2.VideoWriter_fourcc(*"MJPG")

            self.cap.set(cv2.CAP_PROP_FOURCC, mjpg)

            # Read back actual values
            self.width = int(
                self.cap.get(
                    cv2.CAP_PROP_FRAME_WIDTH
                )
            )

            self.height = int(
                self.cap.get(
                    cv2.CAP_PROP_FRAME_HEIGHT
                )
            )

            fourcc = int(
                self.cap.get(
                    cv2.CAP_PROP_FOURCC
                )
            )

            expected_mjpg = cv2.VideoWriter_fourcc(
                *"MJPG"
            )
            if fourcc != expected_mjpg:

                raise RuntimeError("USB camera failed to select MJPG")

            

            return self.width, self.height

    ####################################################################
    # Brightness
    ####################################################################

    def get_brightness(self):

        if not self.initialized or self.cap is None:
            return None

        with self.lock:

            return self.cap.get(
                cv2.CAP_PROP_BRIGHTNESS
            )

    ####################################################################

    def set_brightness(self, value):

        if not self.initialized or self.cap is None:
            return

        value = float(value)

        with self.lock:

            self.cap.set(
                cv2.CAP_PROP_BRIGHTNESS,
                value
            )
    ####################################################################
    # Contrast
    ####################################################################

    def get_contrast(self):

        if not self.initialized or self.cap is None:
            return None

        with self.lock:

            return self.cap.get(
                cv2.CAP_PROP_CONTRAST
            )

    ####################################################################

    def set_contrast(self, value):

        if not self.initialized or self.cap is None:
            return

        value = float(value)

        with self.lock:
            self.cap.set(
                cv2.CAP_PROP_CONTRAST,
                value
            )


    ####################################################################
    # Saturation
    ####################################################################

    def get_saturation(self):

        if not self.initialized or self.cap is None:
            return None
        with self.lock:

            return self.cap.get(
                cv2.CAP_PROP_SATURATION
            )
    ####################################################################

    def set_saturation(self, value):

        if not self.initialized or self.cap is None:
            return

        value = float(value)

        with self.lock:

            self.cap.set(
                cv2.CAP_PROP_SATURATION,
                value
            )

    ####################################################################
    # Exposure
    ####################################################################

    def get_exposure(self):

        if not self.initialized or self.cap is None:
            return None

        with self.lock:

            return self.cap.get(
                cv2.CAP_PROP_EXPOSURE
            )

    ####################################################################

    def set_exposure(self, value):

        if not self.initialized or self.cap is None:
            return

        value = float(value)

        with self.lock:
            self.cap.set(
                cv2.CAP_PROP_EXPOSURE,
                value
            )
    def get_format(self):

        fourcc = int(
            self.cap.get(
                cv2.CAP_PROP_FOURCC
            )
        )

        return "".join(
            chr((fourcc >> (8 * i)) & 0xFF)
            for i in range(4)
        )


class USBWorker(QObject):
        ####################################################################
    # Signals
    ####################################################################

    frameReady = Signal(np.ndarray, float)

    fpsUpdated = Signal(float)

    temperatureUpdated = Signal(float)

    errorOccurred = Signal(str)

    finished = Signal()

    ####################################################################
    # Constructor
    ####################################################################

    def __init__(self, camera):

        super().__init__()

        self.camera = camera

        self.running = False

    ####################################################################
    # Acquisition loop
    ####################################################################

    @Slot()
    def run(self):

        self.running = True

        frame_counter = 0

        fps_timer = time.perf_counter()

        #
        # Initial polling period (seconds)
        #
        # If the camera reports an invalid FPS,
        # fall back to 25 FPS.
        #

        try:

            camera_fps = self.camera.get_framerate()
            

            if camera_fps <= 0:
                camera_fps = 25.0

        except Exception:

            camera_fps = 25.0
        
        
        polling_period = 1.0 / camera_fps

        #
        # Acquisition loop
        #

        while self.running:

            loop_start = time.perf_counter()

            try:
                usb_timestamp = time.perf_counter()
                frame = self.camera.get_frame()

                if frame is not None:

                    self.frameReady.emit(frame, usb_timestamp)

                    frame_counter += 1

            except Exception as e:

                self.errorOccurred.emit(str(e))

                break

            #
            # FPS update every second
            #

            now = time.perf_counter()

            elapsed = now - fps_timer

            if elapsed >= 1.0:

                fps = frame_counter / elapsed

                self.fpsUpdated.emit(fps)

                frame_counter = 0

                fps_timer = now

                #
                # Refresh polling period in case
                # the user changed the frame rate.
                #

                try:

                    camera_fps = self.camera.get_framerate()

                    if camera_fps > 0:

                        polling_period = 1.0 / camera_fps

                except Exception:

                    pass

            #
            # Sleep only for the remaining time
            # in the frame period.
            #

            elapsed = time.perf_counter() - loop_start

            remaining = polling_period - elapsed

            if remaining > 0:

                time.sleep(remaining)

        self.finished.emit()

    ####################################################################
    # Stop acquisition
    ####################################################################

    def stop(self):

        self.running = False

