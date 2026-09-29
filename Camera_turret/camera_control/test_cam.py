import cv2

# 1. Initialize the camera (0 is usually the built-in webcam)
camera = cv2.VideoCapture(0)
camera2 = cv2.VideoCapture(0)


if not camera.isOpened():
    print("Error: Could not open the camera.")
    exit()

print("Camera started. Press 'Space' to take a photo, 'q' to quit.")

#camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0)
#camera.set(cv2.CAP_PROP_EXPOSURE, -8)
print(cv2.CAP_PROP_SETTINGS)

while True:
    # 2. Capture frame-by-frame
    ret, frame = camera.read()
    #ret, frame2 = camera2.read()
    if not ret:
        print("Failed to grab frame.")
        break

    # 3. Display the live feed in a window
    cv2.imshow("Python Camera Control", frame)
    #cv2.imshow("Python Camera Control", frame2)
    # 4. Listen for key presses
    key = cv2.waitKey(1) & 0xFF
    
    if key == ord('q'):  # Quit
        break
    elif key == 32:      # Spacebar pressed to take a snapshot
        img_name = "snapshot.png"
        cv2.imwrite(img_name, frame)
        print(f"Saved {img_name}!")

# 5. Release the camera hardware and close windows
camera.release()
camera2.release()
cv2.destroyAllWindows()