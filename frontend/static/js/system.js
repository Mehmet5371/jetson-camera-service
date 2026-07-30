/* Sistem ekranı: Jetson/JetPack/L4T/Ubuntu bilgisi + kamera bilgisi. */

const SystemTab = (() => {
  function addRow(tbody, label, value) {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = label;
    const td = document.createElement("td");
    td.textContent = value === null || value === undefined || value === "" ? "-" : value;
    tr.appendChild(th);
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  async function load() {
    const systemTbody = document.getElementById("system-info-tbody");
    const cameraTbody = document.getElementById("camera-info-tbody");
    systemTbody.innerHTML = "";
    cameraTbody.innerHTML = "";

    try {
      const info = await Api.get("/api/system");
      addRow(systemTbody, "Hostname", info.hostname);
      addRow(systemTbody, "Jetson Modeli", info.jetson_model);
      addRow(systemTbody, "JetPack Sürümü", info.jetpack_version);
      addRow(systemTbody, "L4T Sürümü", info.l4t_version);
      addRow(systemTbody, "Ubuntu Sürümü", info.ubuntu_version);
      addRow(systemTbody, "IP Adresleri", info.ip_addresses.join(", "));
      addRow(systemTbody, "Sistem Zamanı", info.system_time);
      addRow(systemTbody, "Saat Dilimi", info.timezone);
      addRow(systemTbody, "Sistem Uptime", Format.duration(info.uptime_seconds));
      reportConnectionOk();
    } catch (err) {
      reportConnectionFailure();
      addRow(systemTbody, "Hata", "Sistem bilgisi alınamadı.");
    }

    try {
      const camera = await Api.get("/api/camera/status");
      addRow(cameraTbody, "Backend", camera.backend);
      addRow(cameraTbody, "GStreamer Elementi", camera.source_element);
      addRow(cameraTbody, "Device", camera.device);
      addRow(
        cameraTbody,
        "Sensör Modları",
        camera.sensor_modes.map((m) => `${m.width}x${m.height}@${m.fps}fps`).join(", ")
      );
      if (camera.warnings.length > 0) {
        addRow(cameraTbody, "Uyarılar", camera.warnings.join(" / "));
      }
    } catch (err) {
      addRow(cameraTbody, "Hata", err.message || "Kamera bilgisi alınamadı.");
    }
  }

  return { load };
})();
