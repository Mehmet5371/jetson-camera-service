/*
 * Uygulama kabuğu: sekme geçişleri, auth guard, zorunlu parola değişimi
 * modalı, bağlantı hatası banner'ı (bkz. şartname böl. 11: "Dashboard,
 * backend'e erişilemediğinde yalnızca boş ekran göstermemeli").
 */

const connectionBanner = document.getElementById("connection-banner");
let consecutiveFailures = 0;

function reportConnectionOk() {
  consecutiveFailures = 0;
  connectionBanner.style.display = "none";
}

function reportConnectionFailure() {
  consecutiveFailures += 1;
  if (consecutiveFailures >= 2) {
    connectionBanner.style.display = "block";
  }
}

function switchTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${tabName}`);
  });

  if (tabName === "recordings") RecordingsTab.load();
  if (tabName === "system") SystemTab.load();
  if (tabName === "settings") SettingsTab.load();
  if (tabName === "logs") LogsTab.load();
}

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  try {
    await Api.post("/api/auth/logout");
  } catch (err) {
    // yoksay - her durumda giriş sayfasına dön
  }
  window.location.href = "/login";
});

/* --- Zorunlu parola değişimi --- */

const passwordModal = document.getElementById("password-change-modal");
const passwordForm = document.getElementById("password-change-form");
const passwordError = document.getElementById("password-change-error");

passwordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  passwordError.style.display = "none";
  try {
    await Api.post("/api/auth/change-password", {
      current_password: document.getElementById("current-password").value,
      new_password: document.getElementById("new-password").value,
    });
    passwordModal.classList.remove("visible");
    passwordForm.reset();
    Dashboard.start();
  } catch (err) {
    passwordError.textContent = err.message || "Parola değiştirilemedi.";
    passwordError.style.display = "block";
  }
});

/* --- Başlangıç: kimlik doğrulamayı kontrol et --- */

async function bootstrap() {
  try {
    const me = await Api.get("/api/auth/me");
    document.getElementById("current-username").textContent = me.username;
    reportConnectionOk();

    if (me.must_change_password) {
      passwordModal.classList.add("visible");
    } else {
      Dashboard.start();
    }
  } catch (err) {
    if (err.status === 401) {
      window.location.href = "/login";
      return;
    }
    reportConnectionFailure();
    // 401 dışındaki hatalarda (örn. sunucu henüz ayakta değil) birkaç
    // saniye sonra tekrar dene.
    setTimeout(bootstrap, 3000);
  }
}

bootstrap();

/* --- Genel onay modalı (kayıt silme vb. için recordings.js kullanır) --- */

const confirmModal = document.getElementById("confirm-modal");
const confirmTitle = document.getElementById("confirm-modal-title");
const confirmMessage = document.getElementById("confirm-modal-message");
const confirmBtn = document.getElementById("confirm-modal-confirm-btn");
const cancelBtn = document.getElementById("confirm-modal-cancel-btn");

function askConfirmation(title, message) {
  return new Promise((resolve) => {
    confirmTitle.textContent = title;
    confirmMessage.textContent = message;
    confirmModal.classList.add("visible");

    function cleanup(result) {
      confirmModal.classList.remove("visible");
      confirmBtn.removeEventListener("click", onConfirm);
      cancelBtn.removeEventListener("click", onCancel);
      resolve(result);
    }
    function onConfirm() {
      cleanup(true);
    }
    function onCancel() {
      cleanup(false);
    }
    confirmBtn.addEventListener("click", onConfirm);
    cancelBtn.addEventListener("click", onCancel);
  });
}
