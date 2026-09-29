import cv2

def test_mode(width, height, fourcc_name):

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)

    if not cap.isOpened():
        print("Could not open camera")
        return

    # Resolution first
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    # Format LAST
    fourcc = cv2.VideoWriter_fourcc(*fourcc_name)

    cap.set(
        cv2.CAP_PROP_FOURCC,
        fourcc
    )

    actual_fourcc = int(
        cap.get(cv2.CAP_PROP_FOURCC)
    )

    actual_name = "".join(
        chr((actual_fourcc >> (8 * i)) & 0xFF)
        for i in range(4)
    )

    actual_width = cap.get(
        cv2.CAP_PROP_FRAME_WIDTH
    )

    actual_height = cap.get(
        cv2.CAP_PROP_FRAME_HEIGHT
    )

    print()
    print("Requested:", fourcc_name)
    print("Actual:", actual_name)
    print(
        "Resolution:",
        actual_width,
        "x",
        actual_height
    )

    ret, frame = cap.read()

    if ret:

        print(
            "BGR means:",
            frame[:, :, 0].mean(),
            frame[:, :, 1].mean(),
            frame[:, :, 2].mean()
        )

        cv2.imshow(
            f"{fourcc_name} - press any key",
            frame
        )

        cv2.waitKey(0)
        cv2.destroyAllWindows()

    cap.release()


# Test 1
test_mode(640, 480, "YUY2")

# Test 2
test_mode(640, 480, "MJPG")

# Test 3
test_mode(1280, 720, "YUY2")

# Test 4
test_mode(1280, 720, "MJPG")
