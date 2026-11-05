#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIO_DEFAULT="$HOME/.platformio/penv/bin/pio"
case "${OSTYPE:-}" in
  msys*|cygwin*)
    # Reuse Windows OpenSSH credentials instead of Git's bundled SSH.
    WINDOWS_SSH="$(cygpath -u "$PROGRAMFILES")/OpenSSH"
    if [[ ! -x "$WINDOWS_SSH/ssh.exe" ]]; then
      WINDOWS_SSH="$(cygpath -u "$WINDIR")/System32/OpenSSH"
    fi
    export PATH="$WINDOWS_SSH:$PATH"
    PIO_DEFAULT="$HOME/.platformio/penv/Scripts/platformio.exe"
    ;;
esac
PIO="${PIO:-$PIO_DEFAULT}"
PI_HOST="${PI_HOST:-pi@adrian-pi}"
ENVIRONMENT="${PIO_ENV:-esp32_s3_devkit_usb}"
SERIAL_DEVICE="${INDOOR_SKY_SERIAL_DEVICE:-/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_24:EC:4A:0E:B1:DC-if00}"
BUILD="$ROOT/.pio/build/$ENVIRONMENT"
BOOT_APP0="$HOME/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin"
REMOTE_DIR="/tmp/indoor-sky-flash"

echo "Building indoor-sky firmware..."
"$PIO" run -d "$ROOT" -e "$ENVIRONMENT"

for image in "$BUILD/bootloader.bin" "$BUILD/partitions.bin" "$BUILD/firmware.bin" "$BOOT_APP0"; do
  if [[ ! -f "$image" ]]; then
    echo "Missing flash image: $image" >&2
    exit 1
  fi
done

echo "Copying flash images to $PI_HOST..."
ssh "$PI_HOST" "mkdir -p '$REMOTE_DIR'"
scp "$BUILD/bootloader.bin" "$BUILD/partitions.bin" "$BUILD/firmware.bin" "$BOOT_APP0" "$PI_HOST:$REMOTE_DIR/"

echo "Flashing indoor-sky through the Pi USB connection..."
MSYS_NO_PATHCONV=1 ssh "$PI_HOST" bash -s -- "$REMOTE_DIR" "$SERIAL_DEVICE" <<'REMOTE'
set -Eeuo pipefail
remote_dir="$1"
serial_device="$2"
restart_router() {
  sudo systemctl start router >/dev/null 2>&1 || true
}
trap restart_router EXIT

sudo systemctl stop router
sleep 2
if [[ ! -e "$serial_device" ]]; then
  echo "Indoor-sky serial device is unavailable: $serial_device" >&2
  exit 1
fi

/home/pi/.venvs/esptool/bin/esptool --chip esp32s3 --port "$serial_device" --baud 460800 \
  --before default-reset --after hard-reset write-flash \
  0x0 "$remote_dir/bootloader.bin" \
  0x8000 "$remote_dir/partitions.bin" \
  0xe000 "$remote_dir/boot_app0.bin" \
  0x10000 "$remote_dir/firmware.bin"

rm -rf "$remote_dir"
REMOTE

echo "Indoor-sky USB deployment complete; router service restarted."
