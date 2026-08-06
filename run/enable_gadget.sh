#!/bin/bash -eu

source /root/bin/envsetup.sh

if ! configfs_root=$(findmnt -o TARGET -n configfs)
then
  echo "error: configfs not found"
  exit 1
fi
readonly gadget_root="$configfs_root/usb_gadget/teslausb"

# USB supports many languages. 0x409 is US English
readonly lang=0x409

# configuration name can be anything, the convention
# appears to be to use "c"
readonly cfg=c

if [ -d "$gadget_root" ]
then
  echo "already prepared"
  exit 0
fi

modprobe libcomposite

mkdir -p "$gadget_root/configs/$cfg.1"

# common setup
echo 0x1d6b > "$gadget_root/idVendor"  # Linux Foundation
echo 0x0104 > "$gadget_root/idProduct" # Composite Gadget
echo 0x0100 > "$gadget_root/bcdDevice" # v1.0.0
echo 0x0200 > "$gadget_root/bcdUSB"    # USB 2.0
mkdir -p "$gadget_root/strings/$lang"
mkdir -p "$gadget_root/configs/$cfg.1/strings/$lang"
echo "TeslaUSB-$(sha256sum < /etc/machine-id | awk '{print $1}')" > "$gadget_root/strings/$lang/serialnumber"
echo TeslaUSB > "$gadget_root/strings/$lang/manufacturer"
echo "TeslaUSB Composite Gadget" > "$gadget_root/strings/$lang/product"
echo "TeslaUSB Config" > "$gadget_root/configs/$cfg.1/strings/$lang/configuration"

# A bare Raspberry Pi 4 or 5 can peak at at over 1 A during boot, but idles around 500 mA.
# A Raspberry Pi Zero 2 W can peak at over 300 mA during boot, but idles around 100 mA.
# A Raspberry Pi Zero W can peak up to 220 mA during boot, but idles around 80 mA.
if isPi5
then
  echo 600 > "$gadget_root/configs/$cfg.1/MaxPower"
elif isPi4
then
  echo 500 > "$gadget_root/configs/$cfg.1/MaxPower"
elif isPi2
then
  echo 200 > "$gadget_root/configs/$cfg.1/MaxPower"
else
  echo 100 > "$gadget_root/configs/$cfg.1/MaxPower"
fi

# mass storage setup
mkdir -p "$gadget_root/functions/mass_storage.0"

lun=0

if [ -e "/backingfiles/cam_disk.bin" ]
then
  echo "/backingfiles/cam_disk.bin" > "$gadget_root/functions/mass_storage.0/lun.${lun}/file"
  echo "TeslaUSB CAM $(du -h /backingfiles/cam_disk.bin | awk '{print $1}')" > "$gadget_root/functions/mass_storage.0/lun.${lun}/inquiry_string"
  ((++lun))
fi

# Music/LightShow/Boombox used to each be their own backing image/LUN.
# Consolidated onto one "media_disk.bin" (Music/LightShow/Boombox as
# subfolders) -- Tesla identifies each feature by folder name, not by
# drive identity, so one shared FAT32 volume works the same as three.
# Fewer LUNs means less USB enumeration for the car to do on every wake,
# and one fsck pass instead of three at boot (see ENCRYPTED_CLIPS_ISSUE.md's
# sibling investigation into slow post-wake recognition).
if [ -e "/backingfiles/media_disk.bin" ]
then
  mkdir -p "$gadget_root/functions/mass_storage.0/lun.${lun}"
  echo "/backingfiles/media_disk.bin" > "$gadget_root/functions/mass_storage.0/lun.${lun}/file"
  echo "TeslaUSB MEDIA $(du -h /backingfiles/media_disk.bin | awk '{print $1}')" > "$gadget_root/functions/mass_storage.0/lun.${lun}/inquiry_string"
  ((++lun))
fi

ln -sf "$gadget_root/functions/mass_storage.0" "$gadget_root/configs/$cfg.1"

# activate
find /sys/class/udc -type l -printf '%P\n' | head -1 > "$gadget_root/UDC"
