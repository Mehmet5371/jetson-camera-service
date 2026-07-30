/* Ortak biçimlendirme yardımcıları (dashboard.js, recordings.js, system.js tarafından kullanılır). */

const Format = {
  bytes(value) {
    if (value === null || value === undefined) return "-";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = value;
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex += 1;
    }
    return `${size.toFixed(size >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
  },

  duration(totalSeconds) {
    if (totalSeconds === null || totalSeconds === undefined) return "-";
    const seconds = Math.max(0, Math.floor(totalSeconds));
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
  },

  dateTime(isoString) {
    if (!isoString) return "-";
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return isoString;
    return date.toLocaleString("tr-TR");
  },

  percent(value) {
    if (value === null || value === undefined) return "-";
    return `%${value.toFixed(1)}`;
  },

  temperature(value) {
    if (value === null || value === undefined) return "-";
    return `${value.toFixed(1)}°C`;
  },

  statusBadge(status) {
    const span = document.createElement("span");
    span.className = `badge badge-${status}`;
    span.textContent = status;
    return span.outerHTML;
  },
};
