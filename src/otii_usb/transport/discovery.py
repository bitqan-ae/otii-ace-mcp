# discovery.py: find attached Otii devices via Linux sysfs
#
# Copyright (c) 2026 Milosch Meriac <meriac@bitqan.ae>
#    developed at Bitqan Systems Design (bitqan.ae)
#         From Idea to Manufacturable Product
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0
#
# Walks /sys/class/tty/ttyACM* up to the owning USB device and reads its
# vendor and product id; Otii devices enumerate as 0fce:d1e6. find_otii_port
# returns the single attached device or raises with a helpful message.

"""Find Otii devices among the USB serial ports (Linux sysfs)."""
import glob
import os
from dataclasses import dataclass
from pathlib import Path

from ..errors import DeviceNotFoundError

OTII_USB_IDS: frozenset[tuple[int, int]] = frozenset({(0x0FCE, 0xD1E6)})


@dataclass(frozen=True, slots=True)
class UsbSerialPort:
    path: str
    vendor_id: int
    product_id: int
    manufacturer: str | None = None
    product: str | None = None

    @property
    def is_otii(self) -> bool:
        return (self.vendor_id, self.product_id) in OTII_USB_IDS


def _read_attr(directory: Path, name: str) -> str | None:
    try:
        return (directory / name).read_text().strip()
    except OSError:
        return None


def _usb_device_dir(tty: Path) -> Path | None:
    """Walk up from a tty's sysfs node to the USB device that owns it."""
    node = Path(os.path.realpath(tty / "device"))
    for candidate in (node, *node.parents):
        if (candidate / "idVendor").exists():
            return candidate
        if candidate == Path("/sys"):
            break
    return None


def list_usb_serial_ports(sysfs_root: str = "/sys/class/tty") -> list[UsbSerialPort]:
    ports = []
    for tty in sorted(glob.glob(f"{sysfs_root}/ttyACM*")):
        usb = _usb_device_dir(Path(tty))
        if usb is None:
            continue
        vid, pid = _read_attr(usb, "idVendor"), _read_attr(usb, "idProduct")
        if vid is None or pid is None:
            continue
        ports.append(UsbSerialPort(
            path=f"/dev/{Path(tty).name}", vendor_id=int(vid, 16), product_id=int(pid, 16),
            manufacturer=_read_attr(usb, "manufacturer"), product=_read_attr(usb, "product")))
    return ports


def find_otii_ports() -> list[UsbSerialPort]:
    return [p for p in list_usb_serial_ports() if p.is_otii]


def find_otii_port() -> str:
    """Return the device path of the single attached Otii, or raise."""
    ports = find_otii_ports()
    if not ports:
        raise DeviceNotFoundError("no Otii device found (USB 0fce:d1e6); is it plugged in?")
    if len(ports) > 1:
        paths = ", ".join(p.path for p in ports)
        raise DeviceNotFoundError(f"several Otii devices found ({paths}); choose one explicitly")
    return ports[0].path
