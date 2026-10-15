(function () {
  "use strict";
  if (window.__maintenanceNoticeLoaded || window.top !== window) { return; }
  window.__maintenanceNoticeLoaded = true;

  var WARNING = 300;
  var FINAL_WARNING = 60;
  var COUNTDOWN = 30;

  var plan = null;
  var canManage = false;
  var clockOffset = 0;
  var dialogLevel = null;
  var lastZeroPoll = 0;
  var pollTimer = null;
  var root = null;
  var els = {};

  var CSS = [
    ":host{all:initial}",
    "*{box-sizing:border-box}",
    ".layer{position:fixed;inset:0;z-index:2147483647;display:none;align-items:center;justify-content:center;font-family:Arial,Helvetica,'Microsoft YaHei',sans-serif}",
    ".layer.on{display:flex}",
    ".dim{background:rgba(15,23,32,.55)}",
    ".card{width:min(440px,calc(100vw - 32px));background:#fff;color:#17202a;border-radius:10px;border-top:6px solid #d98e04;padding:22px 24px;box-shadow:0 20px 60px rgba(0,0,0,.35)}",
    ".card.urgent{border-top-color:#c62828}",
    ".card h2{margin:0 0 10px;font-size:20px}",
    ".card p{margin:8px 0;line-height:1.5;font-size:14px}",
    ".card .en{color:#607080}",
    ".card .note{padding:8px 10px;background:#f6f7f9;border-radius:6px}",
    ".card .time{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums;margin:12px 0}",
    ".card button{margin-top:8px;border:0;border-radius:6px;background:#145a7a;color:#fff;font-size:14px;font-weight:700;padding:10px 18px;cursor:pointer}",
    ".full{background:rgba(10,14,20,.92);color:#fff;flex-direction:column;text-align:center;padding:24px}",
    ".full .head{font-size:24px;font-weight:700;margin-bottom:6px}",
    ".full .sub{font-size:15px;opacity:.8;margin-bottom:18px}",
    ".full .num{font-size:min(38vw,220px);line-height:1;font-weight:700;color:#ff6b6b;font-variant-numeric:tabular-nums}",
    ".full .foot{margin-top:18px;font-size:15px;opacity:.85}",
    ".pill{position:fixed;right:16px;bottom:16px;z-index:2147483646;display:none;padding:9px 14px;border-radius:999px;background:#7a4b00;color:#fff;font:700 13px Arial,Helvetica,'Microsoft YaHei',sans-serif;box-shadow:0 4px 14px rgba(0,0,0,.3);font-variant-numeric:tabular-nums}",
    ".pill.on{display:block}"
  ].join("");

  function build() {
    var host = document.createElement("div");
    host.setAttribute("data-maintenance-host", "");
    root = host.attachShadow({ mode: "open" });
    root.innerHTML =
      "<style>" + CSS + "</style>" +
      "<div class='layer dim' id='dialog' role='alertdialog' aria-modal='true'><div class='card' id='card'>" +
      "<h2 id='dTitle'></h2>" +
      "<p id='dZh'></p><p class='en' id='dEn'></p>" +
      "<div class='time' id='dTime'></div>" +
      "<p class='note' id='dNote' hidden></p>" +
      "<button type='button' id='dAck'>知道了 / Got it</button></div></div>" +
      "<div class='layer full' id='full' role='alert'>" +
      "<div class='head'>系统即将重启，请立即保存您的工作</div>" +
      "<div class='sub'>The system is about to restart. Save your work now.</div>" +
      "<div class='num' id='fNum'>30</div>" +
      "<div class='foot' id='fFoot'></div></div>" +
      "<div class='pill' id='pill'></div>";
    document.documentElement.appendChild(host);
    ["dialog", "card", "dTitle", "dZh", "dEn", "dTime", "dNote", "dAck", "full", "fNum", "fFoot", "pill"].forEach(function (id) {
      els[id] = root.getElementById(id);
    });
    els.dAck.addEventListener("click", function () {
      if (plan && dialogLevel) { remember(dialogLevel); }
      dialogLevel = null;
      render();
    });
  }

  function now() { return Date.now() + clockOffset; }
  function remaining() { return plan ? (plan.restart_at * 1000 - now()) / 1000 : Infinity; }
  function ackKey(level) { return "maintenance-ack:" + plan.id + ":" + level; }
  function remember(level) { try { sessionStorage.setItem(ackKey(level), "1"); } catch (e) {} }
  function acked(level) { try { return sessionStorage.getItem(ackKey(level)) === "1"; } catch (e) { return false; } }

  function clock(seconds) {
    var s = Math.max(0, Math.ceil(seconds));
    var m = Math.floor(s / 60);
    return (m < 10 ? "0" : "") + m + ":" + (s % 60 < 10 ? "0" : "") + (s % 60);
  }

  function restartLabel() {
    return new Date(plan.restart_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function render() {
    if (!root) { build(); }
    if (!plan) {
      dialogLevel = null;
      els.dialog.classList.remove("on");
      els.full.classList.remove("on");
      els.pill.classList.remove("on");
      return;
    }
    var left = remaining();
    var label = restartLabel();

    if (left <= 0) {
      els.dialog.classList.remove("on");
      if (canManage) {
        els.full.classList.remove("on");
        els.pill.textContent = "维护模式已开始，仅管理员可访问 / Maintenance mode is active (admins only)";
        els.pill.classList.add("on");
      } else {
        els.pill.classList.remove("on");
        els.fNum.textContent = "0";
        els.fFoot.textContent = "系统重启中 / Restarting…";
        els.full.classList.add("on");
        pollIfDue();
      }
      return;
    }

    if (left <= COUNTDOWN) {
      dialogLevel = null;
      els.dialog.classList.remove("on");
      els.pill.classList.remove("on");
      els.fNum.textContent = String(Math.ceil(left));
      els.fFoot.textContent = "服务将于 " + label + " 下线 / Service goes offline at " + label;
      els.full.classList.add("on");
      return;
    }
    els.full.classList.remove("on");

    if (left > WARNING) {
      dialogLevel = null;
      els.dialog.classList.remove("on");
      els.pill.classList.remove("on");
      return;
    }

    var level = left <= FINAL_WARNING ? "1min" : "5min";
    if (dialogLevel !== level) {
      dialogLevel = acked(level) ? null : level;
      if (dialogLevel) { fillDialog(level, label); }
    }
    if (dialogLevel) {
      els.dTime.textContent = clock(left);
      els.dialog.classList.add("on");
      els.pill.classList.remove("on");
    } else {
      els.dialog.classList.remove("on");
      els.pill.textContent = "系统 " + label + " 重启 · 剩余 " + clock(left) + " / Restart at " + label + " · " + clock(left) + " left";
      els.pill.classList.add("on");
    }
  }

  function fillDialog(level, label) {
    var urgent = level === "1min";
    els.card.className = urgent ? "card urgent" : "card";
    els.dTitle.textContent = urgent ? "⚠ 系统 1 分钟后重启 / Restart in 1 minute" : "⚠ 系统即将重启 / System restart scheduled";
    els.dZh.textContent = "系统将于 " + label + " 重启，请立即保存正在编辑的数据。重启期间系统不可用。";
    els.dEn.textContent = "The system will restart at " + label + ". Please save your work now; the platform will be unavailable during the restart.";
    if (plan.message) {
      els.dNote.textContent = plan.message;
      els.dNote.hidden = false;
    } else {
      els.dNote.hidden = true;
    }
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    var delay = plan && remaining() <= 120 ? 5000 : 15000;
    pollTimer = setTimeout(poll, delay);
  }

  function pollIfDue() {
    var t = Date.now();
    if (t - lastZeroPoll > 3000) { lastZeroPoll = t; poll(); }
  }

  function poll() {
    clearTimeout(pollTimer);
    var sent = Date.now();
    fetch("/api/maintenance", { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (response) {
        if (response.status === 503) {
          window.location.reload();
          return null;
        }
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        if (!data) { return; }
        var received = Date.now();
        clockOffset = data.server_time * 1000 - (sent + received) / 2;
        canManage = !!data.can_manage;
        plan = data.plan;
        if (plan && plan.phase === "maintenance" && !canManage) {
          window.location.reload();
          return;
        }
        render();
      })
      .catch(function () {})
      .then(schedulePoll);
  }

  function start() {
    poll();
    setInterval(function () { if (plan) { render(); } }, 250);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) { poll(); }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
