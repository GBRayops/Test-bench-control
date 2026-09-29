from pyueye import ueye
import time


class UEyeCamera:
    def __init__(self):
        self.hCam = ueye.HIDS(0)

        ret = ueye.is_InitCamera(self.hCam, None)
        if ret != ueye.IS_SUCCESS:
            raise RuntimeError("Failed to initialize camera")

        print("Camera initialized")

    def set_exposure(self, exposure_ms):
        exposure = ueye.double(exposure_ms)

        ret = ueye.is_Exposure(
            self.hCam,
            ueye.IS_EXPOSURE_CMD_SET_EXPOSURE,
            exposure,
            ueye.sizeof(exposure)
        )

        if ret == ueye.IS_SUCCESS:
            print(f"Exposure set to {exposure_ms} ms")
        else:
            print("Failed to set exposure")

    def set_hardware_gain(self, master_gain):
        """
        Gain range: 0–100
        """
        ret = ueye.is_SetHardwareGain(
            self.hCam,
            master_gain,   # master gain
            ueye.IS_IGNORE_PARAMETER,
            ueye.IS_IGNORE_PARAMETER,
            ueye.IS_IGNORE_PARAMETER
        )

        if ret == ueye.IS_SUCCESS:
            print(f"Gain set to {master_gain}")
        else:
            print("Failed to set gain")

    def set_gamma(self, gamma_percent):
        """
        Gamma:
            100 = 1.0
            140 = 1.4
            220 = 2.2
        """
        ret = ueye.is_Gamma(
            self.hCam,
            ueye.IS_GAMMA_CMD_SET,
            ueye.INT(gamma_percent),
            ueye.sizeof(ueye.INT())
        )

        if ret == ueye.IS_SUCCESS:
            print(f"Gamma set to {gamma_percent/100:.2f}")
        else:
            print("Failed to set gamma")

    def set_pixel_clock(self, mhz):
        clk = ueye.UINT(mhz)

        ret = ueye.is_PixelClock(
            self.hCam,
            ueye.IS_PIXELCLOCK_CMD_SET,
            clk,
            ueye.sizeof(clk)
        )

        if ret == ueye.IS_SUCCESS:
            print(f"Pixel clock set to {mhz} MHz")
        else:
            print("Failed to set pixel clock")

    def set_frame_rate(self, fps):
        fps_actual = ueye.double()

        ret = ueye.is_SetFrameRate(
            self.hCam,
            fps,
            fps_actual
        )

        if ret == ueye.IS_SUCCESS:
            print(f"Frame rate = {fps_actual.value:.2f} FPS")
        else:
            print("Failed to set frame rate")

    def start_live(self):
        ret = ueye.is_CaptureVideo(
            self.hCam,
            ueye.IS_DONT_WAIT
        )

        if ret == ueye.IS_SUCCESS:
            print("Live capture started")

    def stop_live(self):
        ueye.is_StopLiveVideo(self.hCam, ueye.IS_FORCE_VIDEO_STOP)

    def close(self):
        self.stop_live()
        ueye.is_ExitCamera(self.hCam)
        print("Camera closed")


if __name__ == "__main__":

    cam = UEyeCamera()

    # Camera settings
    cam.set_pixel_clock(30)      # MHz
    cam.set_frame_rate(30)       # FPS
    cam.set_exposure(10.0)       # milliseconds
    cam.set_hardware_gain(25)    # 0-100
    cam.set_gamma(140)           # Gamma = 1.4

    cam.start_live()

    time.sleep(10)

    cam.close()