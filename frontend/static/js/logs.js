/* Log ekranı: son loglar + seviye filtresi + indirme linki. */

const LogsTab = (() => {
  async function load() {
    const container = document.getElementById("logs-container");
    const level = document.getElementById("logs-level-filter").value;
    const params = new URLSearchParams({ limit: "200" });
    if (level) params.set("level", level);

    try {
      const data = await Api.get(`/api/logs?${params.toString()}`);
      reportConnectionOk();
      container.innerHTML = "";
      if (data.items.length === 0) {
        const empty = document.createElement("div");
        empty.className = "text-muted";
        empty.textContent = "Log bulunamadı.";
        container.appendChild(empty);
        return;
      }
      data.items.forEach((entry) => {
        const line = document.createElement("div");
        line.className = `log-line level-${entry.level || "INFO"}`;
        line.textContent = `[${entry.timestamp}] ${entry.level} ${entry.logger}: ${entry.message}`;
        container.appendChild(line);
      });
    } catch (err) {
      reportConnectionFailure();
      container.innerHTML = '<div class="text-muted">Loglar alınamadı.</div>';
    }
  }

  document.getElementById("logs-refresh-btn").addEventListener("click", load);
  document.getElementById("logs-level-filter").addEventListener("change", load);

  return { load };
})();
