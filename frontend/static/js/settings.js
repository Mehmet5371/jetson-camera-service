/*
 * Ayarlar ekranı: mevcut config'i forma yükler, PUT /api/settings ile
 * kaydeder. Aktif kayıt sırasında kamera/kayıt alanları backend
 * tarafından SETTINGS_LOCKED ile reddedilir - form burada da devre dışı
 * bırakılıp kullanıcıya net bir not gösterilir.
 */

const SettingsTab = (() => {
  function showMessage(text, kind) {
    const box = document.getElementById("settings-message");
    box.textContent = text;
    box.className = `alert alert-${kind}`;
    box.style.display = "block";
  }

  function hideMessage() {
    document.getElementById("settings-message").style.display = "none";
  }

  async function load() {
    hideMessage();
    try {
      const [config, status] = await Promise.all([
        Api.get("/api/settings"),
        Api.get("/api/status"),
      ]);
      reportConnectionOk();

      document.getElementById("setting-width").value = config.camera.width;
      document.getElementById("setting-height").value = config.camera.height;
      document.getElementById("setting-fps").value = config.camera.fps;
      document.getElementById("setting-bitrate").value = config.recording.bitrate_bps;
      document.getElementById("setting-segment").value = config.recording.segment_duration_minutes;
      document.getElementById("setting-min-space").value = config.recording.minimum_free_space_gb;
      document.getElementById("setting-low-space-action").value = config.recording.low_space_action;
      document.getElementById("setting-filename-format").value = config.recording.filename_format;
      document.getElementById("setting-retention-enabled").value = String(config.retention.enabled);
      document.getElementById("setting-retention-percent").value = config.retention.maximum_storage_percent;

      const locked = status.recording.state !== "idle";
      document.getElementById("settings-locked-note").style.display = locked ? "block" : "none";
      ["setting-width", "setting-height", "setting-fps", "setting-bitrate", "setting-segment",
       "setting-min-space", "setting-low-space-action", "setting-filename-format"].forEach((id) => {
        document.getElementById(id).disabled = locked;
      });
    } catch (err) {
      reportConnectionFailure();
      showMessage(err.message || "Ayarlar yüklenemedi.", "error");
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    hideMessage();

    const retentionEnabled = document.getElementById("setting-retention-enabled").value === "true";
    const payload = {
      camera: {
        width: Number(document.getElementById("setting-width").value),
        height: Number(document.getElementById("setting-height").value),
        fps: Number(document.getElementById("setting-fps").value),
      },
      recording: {
        bitrate_bps: Number(document.getElementById("setting-bitrate").value),
        segment_duration_minutes: Number(document.getElementById("setting-segment").value),
        minimum_free_space_gb: Number(document.getElementById("setting-min-space").value),
        low_space_action: document.getElementById("setting-low-space-action").value,
        filename_format: document.getElementById("setting-filename-format").value,
      },
      retention: {
        enabled: retentionEnabled,
        delete_oldest_files: retentionEnabled,
        maximum_storage_percent: Number(document.getElementById("setting-retention-percent").value),
      },
    };

    try {
      await Api.put("/api/settings", payload);
      showMessage("Ayarlar kaydedildi.", "success");
    } catch (err) {
      showMessage(err.message || "Ayarlar kaydedilemedi.", "error");
    }
  }

  document.getElementById("settings-form").addEventListener("submit", handleSubmit);

  return { load };
})();
