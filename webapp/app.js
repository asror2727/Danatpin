const tg = window.Telegram ? window.Telegram.WebApp : null;
if (tg) { tg.ready(); tg.expand(); }

const INIT_DATA = tg ? tg.initData : "";
// only used for local testing outside Telegram, harmless otherwise
const DEBUG_ID = new URLSearchParams(location.search).get("debug_id") || "";

let STATE = {
  user: null,
  games: [],
  currentGame: null,
};

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (INIT_DATA) headers["X-Init-Data"] = INIT_DATA;
  if (!INIT_DATA && DEBUG_ID) headers["X-Debug-Id"] = DEBUG_ID;
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (!res.ok) {
    let e; try { e = await res.json(); } catch { e = {}; }
    throw Object.assign(new Error(e.error || "request_failed"), { status: res.status, data: e });
  }
  return res.json();
}

function fmt(n) {
  return (n || 0).toLocaleString("fr-FR").replace(/,/g, " ");
}

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 2200);
}

/* ============================== INIT ============================== */

async function boot() {
  try {
    const data = await api("/api/init");
    STATE.user = data;
    applyUser(data);
  } catch (e) {
    toast("Ulanishda xatolik. Ilovani qayta oching.");
  }
  await Promise.all([loadGames(), loadBanners(), loadReviews()]);
  bindEvents();
}

function applyUser(u) {
  document.getElementById("userName").textContent = u.full_name || u.username || ("ID " + u.tg_id);
  document.getElementById("profileName").textContent = u.full_name || u.username || ("ID " + u.tg_id);
  document.getElementById("profileId").textContent = "ID: " + u.tg_id;
  ["balanceValue", "balanceValue2", "balanceValue3"].forEach(id => {
    document.getElementById(id).textContent = fmt(u.balance);
  });
  document.getElementById("langBtn").textContent = (u.lang || "uz").toUpperCase();
  document.body.classList.toggle("light", u.theme === "light");
  document.getElementById("themeBtn").textContent = u.theme === "light" ? "☀️" : "🌙";

  const photo = tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.photo_url : "";
  const avatarSrc = photo || "https://api.dicebear.com/7.x/identicon/svg?seed=" + u.tg_id;
  document.getElementById("avatar").src = avatarSrc;
  document.getElementById("avatarBig").src = avatarSrc;

  document.getElementById("channelBtn").href = u.channel_url;
  document.getElementById("supportBtn").href = u.support_url;
}

/* ============================== NAV ============================== */

function showView(name) {
  document.querySelectorAll(".view").forEach(v => v.classList.add("hidden"));
  document.getElementById("view-" + name).classList.remove("hidden");
  document.querySelectorAll(".nav-btn").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  if (name === "top") loadLeaderboard();
  if (name === "orders") loadOrders();
  if (name === "hisob") resetTopupFlow();
}

/* ============================== BANNERS ============================== */

let bannerIndex = 0, bannerTimer = null;

async function loadBanners() {
  const banners = await api("/api/banners");
  const track = document.getElementById("bannerTrack");
  const dots = document.getElementById("bannerDots");
  track.innerHTML = "";
  dots.innerHTML = "";
  if (banners.length === 0) {
    track.innerHTML = `
      <div class="banner-placeholder">
        <span class="banner-placeholder-icon">⚡</span>
        <span class="banner-placeholder-title">ENG TEZ &amp; ARZON</span>
        <span class="banner-placeholder-sub">Barcha o'yinlar uchun xizmatlar</span>
      </div>`;
    return;
  }
  banners.forEach((b, i) => {
    const img = document.createElement("img");
    img.src = b.image_url;
    if (i === 0) img.classList.add("active");
    track.appendChild(img);
    const dot = document.createElement("span");
    if (i === 0) dot.classList.add("active");
    dots.appendChild(dot);
  });
  bannerIndex = 0;
  clearInterval(bannerTimer);
  if (banners.length > 1) {
    bannerTimer = setInterval(() => {
      const imgs = track.querySelectorAll("img");
      const dotEls = dots.querySelectorAll("span");
      imgs[bannerIndex].classList.remove("active");
      dotEls[bannerIndex].classList.remove("active");
      bannerIndex = (bannerIndex + 1) % imgs.length;
      imgs[bannerIndex].classList.add("active");
      dotEls[bannerIndex].classList.add("active");
    }, 3500);
  }
}

/* ============================== GAMES ============================== */

const GAME_EMOJI = [
  { match: /pubg/i, icon: "🎯", from: "#f97316", to: "#7c2d12" },
  { match: /free ?fire/i, icon: "🔥", from: "#f43f5e", to: "#7f1d1d" },
  { match: /mobile ?legends|mlbb/i, icon: "⚔️", from: "#3b82f6", to: "#1e3a8a" },
  { match: /honor ?of ?kings/i, icon: "👑", from: "#eab308", to: "#78350f" },
  { match: /stand ?off/i, icon: "🔫", from: "#64748b", to: "#0f172a" },
  { match: /steam/i, icon: "🎮", from: "#0ea5e9", to: "#0c4a6e" },
  { match: /telegram/i, icon: "✈️", from: "#38bdf8", to: "#0369a1" },
];

function gameVisual(name) {
  const found = GAME_EMOJI.find(g => g.match.test(name));
  if (found) return found;
  return { icon: "🎮", from: "#8b5cf6", to: "#4c1d95" };
}

async function loadGames() {
  const games = await api("/api/games");
  STATE.games = games;
  const grid = document.getElementById("gamesGrid");
  grid.innerHTML = "";
  games.forEach(g => grid.appendChild(renderGameTile(g)));
}

function renderGameTile(g) {
  const el = document.createElement("div");
  el.className = "game-item";
  const v = gameVisual(g.name);
  const hasImg = g.image_url && g.image_url.trim() !== "";
  el.innerHTML = `
    <div class="game-thumb-wrap">
      ${hasImg ? `<img class="game-thumb" src="${g.image_url}" alt="${g.name}">` : ""}
      <div class="game-thumb-fallback" style="background:linear-gradient(145deg,${v.from},${v.to});${hasImg ? "display:none;" : ""}">${v.icon}</div>
      <span class="rating-badge">⭐ 5.0</span>
    </div>
    <div class="game-name">${g.name}</div>
  `;
  if (hasImg) {
    const img = el.querySelector(".game-thumb");
    img.addEventListener("error", () => {
      img.style.display = "none";
      el.querySelector(".game-thumb-fallback").style.display = "flex";
    });
  }
  el.addEventListener("click", () => openGame(g.id));
  return el;
}

function openGame(id) {
  const g = STATE.games.find(x => x.id === id);
  if (!g) return;
  STATE.currentGame = g;
  document.getElementById("gameOverlayTitle").textContent = g.name;
  const bannerImg = document.getElementById("gameOverlayImg");
  const bannerFallback = document.getElementById("gameOverlayFallback");
  const v = gameVisual(g.name);
  const showFallback = () => {
    bannerImg.classList.add("hidden");
    bannerFallback.classList.remove("hidden");
    bannerFallback.style.background = `linear-gradient(145deg,${v.from},${v.to})`;
    bannerFallback.textContent = v.icon;
  };
  if (g.image_url && g.image_url.trim() !== "") {
    bannerImg.src = g.image_url;
    bannerImg.classList.remove("hidden");
    bannerFallback.classList.add("hidden");
    bannerImg.onerror = showFallback;
  } else {
    showFallback();
  }
  const grid = document.getElementById("packagesGrid");
  grid.innerHTML = "";
  g.packages.forEach((p, idx) => {
    const el = document.createElement("button");
    el.className = "package-card";
    el.innerHTML = `
      <div class="p-icon">🪙</div>
      <div class="p-label">${p.label}</div>
      <div class="p-price">${fmt(p.price)} so'm</div>
    `;
    el.addEventListener("click", () => buyPackage(g.id, idx));
    grid.appendChild(el);
  });
  document.getElementById("gameOverlay").classList.remove("hidden");
}

async function buyPackage(gameId, packageIndex) {
  try {
    const res = await api("/api/order", {
      method: "POST",
      body: JSON.stringify({ game_id: gameId, package_index: packageIndex }),
    });
    STATE.user.balance = res.balance;
    applyUser(STATE.user);
    document.getElementById("gameOverlay").classList.add("hidden");
    toast("✅ Buyurtma muvaffaqiyatli amalga oshirildi!");
  } catch (e) {
    if (e.data && e.data.error === "insufficient_balance") {
      toast("❌ Balansingiz yetarli emas. Avval to'ldiring.");
    } else {
      toast("❌ Xatolik yuz berdi");
    }
  }
}

/* ============================== REVIEWS ============================== */

async function loadReviews() {
  const reviews = await api("/api/reviews");
  const list = document.getElementById("reviewsList");
  list.innerHTML = "";
  if (reviews.length === 0) {
    list.innerHTML = '<div class="empty-note">Hali fikrlar yo\'q. Birinchi bo\'ling!</div>';
    return;
  }
  reviews.slice(0, 8).forEach(r => {
    const el = document.createElement("div");
    el.className = "review-item";
    const name = r.full_name || r.username || "Foydalanuvchi";
    el.innerHTML = `
      <div class="review-name">${name}</div>
      <div class="review-text">${escapeHtml(r.text)}</div>
      <div class="review-stars">${"⭐".repeat(Math.max(1, Math.min(5, r.rating)))}</div>
    `;
    list.appendChild(el);
  });
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

/* ============================== LEADERBOARD ============================== */

async function loadLeaderboard() {
  const list = await api("/api/leaderboard");
  const el = document.getElementById("leaderboardList");
  el.innerHTML = "";
  if (list.length === 0) {
    el.innerHTML = '<div class="empty-note">Hozircha xaridorlar yo\'q</div>';
    return;
  }
  const medals = ["🥇", "🥈", "🥉"];
  list.forEach((u, i) => {
    const name = u.full_name || u.username || ("ID " + u.tg_id);
    const rankHtml = i < 3 ? medals[i] : "#" + (i + 1);
    const row = document.createElement("div");
    row.className = "lb-item";
    row.innerHTML = `
      <div class="lb-rank ${i < 3 ? "medal" : ""}">${rankHtml}</div>
      <img class="lb-avatar" src="https://api.dicebear.com/7.x/identicon/svg?seed=${u.tg_id}">
      <div class="lb-info">
        <div class="lb-name">${name}</div>
        <div class="lb-meta">${u.order_count} buyurtma</div>
      </div>
      <div class="lb-amount">${fmt(u.total_spent)} UZS</div>
    `;
    el.appendChild(row);
  });
}

/* ============================== ORDERS ============================== */

async function loadOrders() {
  const orders = await api("/api/orders");
  const el = document.getElementById("ordersList");
  el.innerHTML = "";
  if (orders.length === 0) {
    el.innerHTML = '<div class="empty-note">Buyurtmalar mavjud emas</div>';
    return;
  }
  orders.forEach(o => {
    const row = document.createElement("div");
    row.className = "order-item";
    const date = new Date(o.created_at).toLocaleString("uz-UZ", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    row.innerHTML = `
      <div>
        <div class="order-game">${o.game_name || "Xizmat"} — ${o.package_label || ""}</div>
        <div class="order-meta">${date}</div>
      </div>
      <div class="order-price">${fmt(o.price)} so'm</div>
    `;
    el.appendChild(row);
  });
}

/* ============================== TOPUP FLOW ============================== */

let topupState = { method: "UZCARD", amount: 0, timerInterval: null, card: null };

function resetTopupFlow() {
  show("hisob-step-methods");
}

function show(id) {
  ["hisob-step-methods", "hisob-step-amount", "hisob-step-pay"].forEach(x => {
    document.getElementById(x).classList.toggle("hidden", x !== id);
  });
}

async function goToAmountStep(method) {
  topupState.method = method;
  document.getElementById("chosenMethod").textContent = method;
  document.getElementById("amountInput").value = "";
  show("hisob-step-amount");
}

async function goToPayStep() {
  const raw = document.getElementById("amountInput").value.replace(/\D/g, "");
  const amount = parseInt(raw || "0", 10);
  if (amount < 1000) {
    toast("Eng kam summa 1 000 so'm");
    return;
  }
  topupState.amount = amount;

  if (!topupState.card) {
    topupState.card = await api("/api/topup-info");
  }
  document.getElementById("payAmount").textContent = fmt(amount);
  document.getElementById("cardNumber").textContent = topupState.card.card_number;
  document.getElementById("cardHolder").textContent = topupState.card.card_holder;
  document.getElementById("cardBank").textContent = topupState.card.card_bank;

  show("hisob-step-pay");
  startPayTimer(180);
}

function startPayTimer(seconds) {
  clearInterval(topupState.timerInterval);
  let remaining = seconds;
  const total = seconds;
  const label = document.getElementById("payTimer");
  const fill = document.getElementById("progressFill");
  const tick = () => {
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    label.textContent = `${m}:${s.toString().padStart(2, "0")}`;
    fill.style.width = (remaining / total * 100) + "%";
    if (remaining <= 0) {
      clearInterval(topupState.timerInterval);
      toast("⏱ Vaqt tugadi, qaytadan urinib ko'ring");
      resetTopupFlow();
      return;
    }
    remaining--;
  };
  tick();
  topupState.timerInterval = setInterval(tick, 1000);
}

async function confirmPaid() {
  try {
    await api("/api/topup/request", {
      method: "POST",
      body: JSON.stringify({ amount: topupState.amount, method: topupState.method }),
    });
    clearInterval(topupState.timerInterval);
    toast("✅ So'rovingiz qabul qilindi, tez orada tasdiqlanadi");
    resetTopupFlow();
  } catch (e) {
    toast("❌ Xatolik yuz berdi");
  }
}

/* ============================== EVENTS ============================== */

function bindEvents() {
  document.querySelectorAll(".nav-btn").forEach(btn => {
    btn.addEventListener("click", () => showView(btn.dataset.view));
  });

  document.getElementById("topupBtnHome").addEventListener("click", () => showView("hisob"));
  document.getElementById("topupBtnProfile").addEventListener("click", () => showView("hisob"));
  document.getElementById("topDonatersBtn").addEventListener("click", () => showView("top"));
  document.getElementById("seeAllGames").addEventListener("click", () => {
    document.getElementById("gamesGrid").scrollIntoView({ behavior: "smooth" });
  });

  document.getElementById("openProfile").addEventListener("click", () => showView("profile"));

  document.getElementById("langBtn").addEventListener("click", async () => {
    const next = STATE.user.lang === "uz" ? "ru" : "uz";
    const res = await api("/api/lang", { method: "POST", body: JSON.stringify({ lang: next }) });
    STATE.user.lang = res.lang;
    document.getElementById("langBtn").textContent = res.lang.toUpperCase();
  });

  document.getElementById("themeBtn").addEventListener("click", async () => {
    const next = document.body.classList.contains("light") ? "dark" : "light";
    const res = await api("/api/theme", { method: "POST", body: JSON.stringify({ theme: next }) });
    STATE.user.theme = res.theme;
    document.body.classList.toggle("light", res.theme === "light");
    document.getElementById("themeBtn").textContent = res.theme === "light" ? "☀️" : "🌙";
  });

  document.querySelectorAll(".method-card").forEach(btn => {
    btn.addEventListener("click", () => goToAmountStep(btn.dataset.method));
  });
  document.querySelectorAll(".chip").forEach(btn => {
    btn.addEventListener("click", () => {
      document.getElementById("amountInput").value = btn.dataset.amt;
    });
  });
  document.getElementById("goToPayment").addEventListener("click", goToPayStep);
  document.getElementById("backToMethods").addEventListener("click", () => show("hisob-step-methods"));
  document.getElementById("confirmPaidBtn").addEventListener("click", confirmPaid);
  document.getElementById("copyCardBtn").addEventListener("click", () => {
    const num = document.getElementById("cardNumber").textContent;
    navigator.clipboard && navigator.clipboard.writeText(num.replace(/\s/g, ""));
    toast("📋 Nusxalandi");
  });

  document.getElementById("closeGameOverlay").addEventListener("click", () => {
    document.getElementById("gameOverlay").classList.add("hidden");
  });

  document.getElementById("leaveReviewBtn").addEventListener("click", () => {
    document.getElementById("reviewOverlay").classList.remove("hidden");
  });
  document.getElementById("closeReviewOverlay").addEventListener("click", () => {
    document.getElementById("reviewOverlay").classList.add("hidden");
  });
  document.getElementById("submitReviewBtn").addEventListener("click", async () => {
    const text = document.getElementById("reviewText").value.trim();
    if (!text) return;
    try {
      await api("/api/reviews", { method: "POST", body: JSON.stringify({ text, rating: 5 }) });
      document.getElementById("reviewText").value = "";
      document.getElementById("reviewOverlay").classList.add("hidden");
      toast("✅ Rahmat! Fikringiz qo'shildi");
      loadReviews();
    } catch (e) {
      toast("❌ Xatolik yuz berdi");
    }
  });

  document.getElementById("inviteBtn").addEventListener("click", () => {
    const link = `https://t.me/share/url?url=Assalomu alaykum! Mening havolam orqali ro'yxatdan o'ting va bonus oling.`;
    if (tg && tg.openTelegramLink) tg.openTelegramLink(link);
    else window.open(link, "_blank");
  });
}

boot();
