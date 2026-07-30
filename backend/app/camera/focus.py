"""Arducam IMX519 VCM (voice coil motor) odak motoru kontrolü.

Bu modül, Arducam'ın resmi örneği (~/Jetson_IMX519_Focus_Example/Focuser.py)
ile AYNI I2C register dizisini ve ölçeklemeyi kullanır (I2C adresi 0x0c,
register 0x00/0x01/0x02, 0-1000 mantıksal değerin 12-bit DAC'a çevrilip
16-bit'e sola kaydırılarak iki byte halinde yazılması). Fark: orijinal
örnek `os.system("i2cset -y {bus} {addr} {reg} {val}".format(...))`
kullanıyor (shell string enterpolasyonu); bu proje shell=True veya shell
string birleştirmeyi yasakladığı için aynı komutlar subprocess ile
argüman LİSTESİ olarak çalıştırılır.

Odak motoru, görüntü sensöründen (I2C adres 0x1a, kernel sürücüsüne bağlı)
AYRI bir cihazdır: adres 0x0c hiçbir kernel sürücüsü tarafından claim
edilmemiştir (Faz 1'de `i2cdetect -y -r 10` ile doğrulandı), bu yüzden
doğrudan userspace'ten erişilebilir ve sensör sürücüsüyle çakışmaz.
"""

from __future__ import annotations

from app.core.errors import AppError, ErrorCode
from app.core.shell import run_command

_FOCUSER_I2C_ADDRESS = "0x0c"
_INIT_REGISTER = "0x02"
_HIGH_BYTE_REGISTER = "0x00"
_LOW_BYTE_REGISTER = "0x01"

MIN_FOCUS_VALUE = 0
MAX_FOCUS_VALUE = 1000


class FocuserError(AppError):
    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(
            ErrorCode.FOCUS_CONTROL_UNAVAILABLE,
            message,
            status_code=503,
            details=details or {},
        )


def _i2c_set(bus: int, register: str, value_hex: str) -> None:
    result = run_command(
        ["i2cset", "-y", str(bus), _FOCUSER_I2C_ADDRESS, register, value_hex],
        timeout=2.0,
    )
    if not result.ok:
        raise FocuserError(
            f"I2C yazma başarısız (bus={bus}, addr={_FOCUSER_I2C_ADDRESS}, reg={register}).",
            details={"stderr": result.stderr.strip(), "returncode": result.returncode},
        )


def _scale_to_dac_bytes(value: int) -> tuple[int, int]:
    """0-1000 mantıksal değeri Arducam'ın 12-bit DAC formatına çevirir.

    Orijinal Focuser.write(): value = int(value/1000*4095); sonra module-level
    write(): value <<= 4; register 0x00 = value >> 8; register 0x01 = value & 0xFF.
    """
    dac_value = int(value / 1000.0 * 4095)
    shifted = dac_value << 4
    high_byte = (shifted >> 8) & 0xFF
    low_byte = shifted & 0xFF
    return high_byte, low_byte


class FocusController:
    """Tek bir I2C bus üzerindeki Arducam VCM odak motorunu kontrol eder."""

    def __init__(self, i2c_bus: int) -> None:
        self._bus = i2c_bus
        self._initialized = False
        self._current_value: int | None = None

    def initialize(self) -> None:
        _i2c_set(self._bus, _INIT_REGISTER, "0x00")
        self._initialized = True

    def mark_stream_restarted(self) -> None:
        """Yeni bir Argus akışı başladığında çağrılır.

        VCM odak çipi akış yeniden başladığında sıfırlanmış olabilir - bir
        sonraki set_focus çağrısının initialize()'ı tekrar çalıştırmasını
        (register 0x02) sağlar. `current_value` KORUNUR (önizlemede bulunan
        odak değeri kayıt başlarken yeniden uygulanabilsin diye).
        """
        self._initialized = False

    def reapply(self, default_value: int) -> None:
        """Bilinen son odak değerini (yoksa default_value) yeniden yazar.

        Akış yeniden başladığında lens fiziksel olarak dinlenme konumuna
        (bulanık) döner - bu, o değeri geri uygular. Önizlemede autofocus/
        manuel ile bulunan değer kayıt başlarken böyle korunur.
        """
        target = self._current_value if self._current_value is not None else default_value
        self.set_focus(target)

    def set_focus(self, value: int) -> None:
        if not (MIN_FOCUS_VALUE <= value <= MAX_FOCUS_VALUE):
            raise ValueError(
                f"focus value must be within [{MIN_FOCUS_VALUE}, {MAX_FOCUS_VALUE}], got {value}"
            )
        if not self._initialized:
            self.initialize()

        high_byte, low_byte = _scale_to_dac_bytes(value)
        _i2c_set(self._bus, _HIGH_BYTE_REGISTER, f"0x{high_byte:02x}")
        _i2c_set(self._bus, _LOW_BYTE_REGISTER, f"0x{low_byte:02x}")
        self._current_value = value

    @property
    def current_value(self) -> int | None:
        return self._current_value
