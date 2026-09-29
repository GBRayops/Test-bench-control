import threading


class CameraController:

    def __init__(self, ids_camera, usb_camera):

        self.ids_camera = ids_camera
        self.usb_camera = usb_camera

        self.initialized = False
        self.running = False

        self.lock = threading.Lock()

        self.ids_settings = {}

    ####################################################################
    # IDS settings
    ####################################################################

    def set_ids_settings(
        self,
        exposure,
        gain,
        fps,
        pixel_clock
    ):

        self.ids_settings = {
            "exposure": float(exposure),
            "gain": int(gain),
            "fps": float(fps),
            "pixel_clock": int(pixel_clock),
        }

    ####################################################################
    # Apply IDS settings
    ####################################################################

    def apply_ids_settings(self):

        if not self.ids_camera.initialized:
            raise RuntimeError(
                "IDS camera is not initialized"
            )

        if not self.ids_settings:
            raise RuntimeError(
                "No IDS camera settings have been configured"
            )

        settings = self.ids_settings

        #
        # IMPORTANT:
        # Apply these in this order because the IDS timing
        # parameters are interdependent.
        #

        # 1. Pixel clock
        self.ids_camera.set_pixel_clock(
            settings["pixel_clock"]
        )

        # 2. Frame rate
        self.ids_camera.set_framerate(
            settings["fps"]
        )

        # 3. Exposure
        self.ids_camera.set_exposure(
            settings["exposure"]
        )

        # 4. Gain
        self.ids_camera.set_gain(
            settings["gain"]
        )

        #
        # Read back the actual values accepted by the camera.
        #

        return {
            "pixel_clock":
                self.ids_camera.get_pixel_clock(),

            "fps":
                self.ids_camera.get_framerate(),

            "exposure":
                self.ids_camera.get_exposure(),

            "gain":
                self.ids_camera.get_gain(),
        }

    ####################################################################
    # Open both cameras
    ####################################################################



    def open(self):

        with self.lock:

            if self.initialized:
                return

            try:

                #
                # Open IDS
                #

                self.ids_camera.open()

                #
                # Open USB
                #

                try:

                    self.usb_camera.open()

                except Exception:

                    # If USB fails, don't leave IDS open.
                    self.ids_camera.close()

                    raise

                self.initialized = True

            except Exception:

                self.initialized = False

                raise

    ####################################################################
    # Start both cameras
    ####################################################################

    def start(self):

        if not self.initialized:
            raise RuntimeError(
                "Cameras are not initialized"
            )

        if self.running:
            return

        try:

            #
            # Make sure IDS settings have been
            # supplied before starting acquisition.
            #

            if not self.ids_settings:

                raise RuntimeError(
                    "IDS camera settings have not been configured"
                )

            #
            # Apply IDS settings BEFORE acquisition.
            #

            self.apply_ids_settings()

            #
            # Start IDS acquisition
            #

            self.ids_camera.start()

            #
            # Start USB camera
            #

            try:

                self.usb_camera.start()

            except Exception:

                self.ids_camera.stop()

                raise

            self.running = True

        except Exception:

            self.running = False

            raise

    ####################################################################
    # Stop both cameras
    ####################################################################

    def stop(self):

        if not self.running:
            return

        errors = []

        #
        # Stop IDS
        #

        try:

            self.ids_camera.stop()

        except Exception as e:

            errors.append(
                f"IDS camera: {e}"
            )

        #
        # Stop USB
        #

        try:

            self.usb_camera.stop()

        except Exception as e:

            errors.append(
                f"USB camera: {e}"
            )

        self.running = False

        if errors:

            raise RuntimeError(
                "\n".join(errors)
            )

    ####################################################################
    # Close both cameras
    ####################################################################

    def close(self):

        errors = []

        #
        # Stop IDS
        #

        try:

            self.ids_camera.stop()

        except Exception as e:

            errors.append(
                f"IDS stop: {e}"
            )

        #
        # Stop USB
        #

        try:

            self.usb_camera.stop()

        except Exception as e:

            errors.append(
                f"USB stop: {e}"
            )

        #
        # Close IDS
        #

        try:

            self.ids_camera.close()

        except Exception as e:

            errors.append(
                f"IDS close: {e}"
            )

        #
        # Close USB
        #

        try:

            self.usb_camera.close()

        except Exception as e:

            errors.append(
                f"USB close: {e}"
            )

        self.running = False
        self.initialized = False

        if errors:

            raise RuntimeError(
                "\n".join(errors)
            )