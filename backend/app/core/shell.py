"""Harici komut çalıştırma yardımcıları.

Kural (bkz. proje şartnamesi böl. 24): shell=True KULLANILMAZ, kullanıcı
girdisi asla doğrudan bir komut argümanına ham string olarak eklenmez.
Tüm çağıranlar sabit binary yolları + doğrulanmış (Pydantic ile kontrol
edilmiş sayısal/enum) parametrelerle bir argüman LİSTESİ geçer.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_command(args: list[str], *, timeout: float = 10.0) -> CommandResult:
    """subprocess.run etrafında ince bir sarmalayıcı.

    args her zaman bir liste olmalıdır (tek bir shell string DEĞİL).
    Komut bulunamazsa veya zaman aşımına uğrarsa exception fırlatmak yerine
    bunu CommandResult.ok == False olarak döndürür; çağıran kod (kamera
    algılama, fokus kontrolü, kayıt yöneticisi) bunu kendi hata tipine
    çevirip anlamlı bir mesaj üretir.
    """
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError as exc:
        return CommandResult(returncode=127, stdout="", stderr=str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        return CommandResult(
            returncode=124, stdout=stdout, stderr=f"command timed out after {timeout}s"
        )

    return CommandResult(completed.returncode, completed.stdout, completed.stderr)
