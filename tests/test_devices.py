from simple_photos.core.devices import Volume, camera_name, pick_devices


def test_linux_desktop_mounts_and_gvfs_cameras(monkeypatch):
    # gvfs's folder names contain ":", which Windows can't create — so the
    # listing is faked rather than made on disk.
    import simple_photos.core.devices as devices

    gvfs = "/run/user/1000/gvfs"
    entries = {
        gvfs: [
            "gphoto2:host=Canon_Inc._Canon_EOS_R6",
            "mtp:host=Google_Pixel_7_2B1C3D4E5F",
            "smb-share:server=nas,share=photos",  # a network share: not a device
        ]
    }
    monkeypatch.setattr(devices, "_list_dir", lambda path: entries.get(path, []))
    volumes = [
        Volume("/"),
        Volume("/home"),
        Volume("/boot/efi"),
        Volume("/media/mike/KINGSTON", "KINGSTON"),
        Volume("/run/media/mike/EOS_DIGITAL", ""),
        Volume("/snap/core/123", read_only=True),
    ]
    found = pick_devices(volumes, platform="linux", gvfs_dir=gvfs)
    assert [(d.name, d.kind) for d in found] == [
        ("KINGSTON", "drive"),
        ("EOS_DIGITAL", "drive"),
        ("Canon Inc. Canon EOS R6", "camera"),
        ("Google Pixel 7", "camera"),
    ]


def test_macos_skips_the_startup_disk_and_disk_images():
    volumes = [
        Volume("/"),
        Volume("/System/Volumes/Data"),
        Volume("/Volumes/Macintosh HD", read_only=True),
        Volume("/Volumes/Simple-Photos", read_only=True),  # a mounted installer .dmg
        Volume("/Volumes/SD CARD", "SD CARD"),
        Volume("/Volumes/.timemachine"),
    ]
    assert [d.name for d in pick_devices(volumes, platform="darwin")] == ["SD CARD"]


def test_windows_skips_the_system_drive_and_network_drives():
    types = {"D:\\": 2, "E:\\": 3, "F:\\": 4, "G:\\": 5}  # removable, fixed, network, CD
    volumes = [Volume("C:/", "Windows"), Volume("D:/", "KINGSTON"), Volume("E:/", "Backup"), Volume("F:/"), Volume("G:/")]
    devices = pick_devices(volumes, platform="win32", system_drive="C:", drive_type=types.get)
    assert [(d.path, d.name, d.description) for d in devices] == [
        ("D:\\", "KINGSTON", "KINGSTON (D:)"),
        ("E:\\", "Backup", "Backup (E:)"),
    ]


def test_camera_names():
    assert camera_name("mtp:host=SAMSUNG_SAMSUNG_Android_R58N12ABCDE") == "SAMSUNG SAMSUNG Android"
    assert camera_name("gphoto2:host=%5Busb%3A001%2C012%5D") == "Camera"
    assert camera_name("mtp:host=Apple_Inc._iPhone") == "Apple Inc. iPhone"
