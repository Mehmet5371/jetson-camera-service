/*
 * Kayıtlar ekranı: listeleme (sayfalama/arama/filtre/sıralama), oynatma,
 * indirme, silme.
 */

const RecordingsTab = (() => {
  const state = {
    page: 1,
    pageSize: 10,
    sortBy: "started_at",
    sortOrder: "desc",
    search: "",
    statusFilter: "",
  };

  function renderRow(recording) {
    const tr = document.createElement("tr");

    const nameTd = document.createElement("td");
    nameTd.textContent = recording.filename;
    tr.appendChild(nameTd);

    const dateTd = document.createElement("td");
    dateTd.textContent = Format.dateTime(recording.started_at);
    tr.appendChild(dateTd);

    const durationTd = document.createElement("td");
    durationTd.textContent = Format.duration(recording.duration_seconds);
    tr.appendChild(durationTd);

    const sizeTd = document.createElement("td");
    sizeTd.textContent = Format.bytes(recording.file_size_bytes);
    tr.appendChild(sizeTd);

    const resolutionTd = document.createElement("td");
    resolutionTd.textContent =
      recording.width && recording.height ? `${recording.width}x${recording.height}` : "-";
    tr.appendChild(resolutionTd);

    const statusTd = document.createElement("td");
    statusTd.innerHTML = Format.statusBadge(recording.status);
    tr.appendChild(statusTd);

    const actionsTd = document.createElement("td");
    actionsTd.className = "btn-row";
    actionsTd.style.marginTop = "0";

    const playBtn = document.createElement("button");
    playBtn.className = "btn";
    playBtn.textContent = "İzle";
    playBtn.disabled = !recording.is_valid;
    playBtn.addEventListener("click", () => playRecording(recording));
    actionsTd.appendChild(playBtn);

    const downloadLink = document.createElement("a");
    downloadLink.className = "btn";
    downloadLink.textContent = "İndir";
    downloadLink.href = `/api/recordings/${recording.id}/download`;
    actionsTd.appendChild(downloadLink);

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "btn btn-danger";
    deleteBtn.textContent = "Sil";
    deleteBtn.addEventListener("click", () => deleteRecording(recording));
    actionsTd.appendChild(deleteBtn);

    tr.appendChild(actionsTd);
    return tr;
  }

  function playRecording(recording) {
    const card = document.getElementById("recording-player-card");
    const title = document.getElementById("recording-player-title");
    const player = document.getElementById("recording-player");
    title.textContent = recording.filename;
    player.src = `/api/recordings/${recording.id}/stream`;
    card.style.display = "block";
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    player.play().catch(() => {
      // otomatik oynatma engellenmiş olabilir, kullanıcı manuel başlatır
    });
  }

  async function deleteRecording(recording) {
    const confirmed = await askConfirmation(
      "Kaydı sil",
      `"${recording.filename}" kalıcı olarak silinecek. Bu işlem geri alınamaz.`
    );
    if (!confirmed) return;
    try {
      await Api.del(`/api/recordings/${recording.id}`);
      load();
    } catch (err) {
      alert(err.message || "Kayıt silinemedi.");
    }
  }

  async function load() {
    const tbody = document.getElementById("recordings-tbody");
    const params = new URLSearchParams({
      page: state.page,
      page_size: state.pageSize,
      sort_by: state.sortBy,
      sort_order: state.sortOrder,
    });
    if (state.search) params.set("search", state.search);
    if (state.statusFilter) params.set("status", state.statusFilter);

    try {
      const data = await Api.get(`/api/recordings?${params.toString()}`);
      tbody.innerHTML = "";
      if (data.items.length === 0) {
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 7;
        td.className = "text-muted";
        td.textContent = "Kayıt bulunamadı.";
        tr.appendChild(td);
        tbody.appendChild(tr);
      } else {
        data.items.forEach((recording) => tbody.appendChild(renderRow(recording)));
      }

      const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));
      document.getElementById("recordings-page-info").textContent = `Sayfa ${data.page} / ${totalPages} (${data.total} kayıt)`;
      document.getElementById("recordings-prev-btn").disabled = data.page <= 1;
      document.getElementById("recordings-next-btn").disabled = data.page >= totalPages;
    } catch (err) {
      reportConnectionFailure();
    }
  }

  document.getElementById("recordings-refresh-btn").addEventListener("click", load);
  document.getElementById("recordings-prev-btn").addEventListener("click", () => {
    if (state.page > 1) {
      state.page -= 1;
      load();
    }
  });
  document.getElementById("recordings-next-btn").addEventListener("click", () => {
    state.page += 1;
    load();
  });
  document.getElementById("recordings-search").addEventListener("input", (event) => {
    state.search = event.target.value;
    state.page = 1;
    load();
  });
  document.getElementById("recordings-status-filter").addEventListener("change", (event) => {
    state.statusFilter = event.target.value;
    state.page = 1;
    load();
  });
  document.querySelectorAll("th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const column = th.dataset.sort;
      if (state.sortBy === column) {
        state.sortOrder = state.sortOrder === "asc" ? "desc" : "asc";
      } else {
        state.sortBy = column;
        state.sortOrder = "desc";
      }
      load();
    });
  });

  return { load };
})();
