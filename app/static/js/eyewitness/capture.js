// Eyewitness customer capture: instant blur/brightness check, upload,
// then poll for the server verdict. No Meta key ever reaches the browser.
(function () {
  var token = location.pathname.split("/").filter(Boolean)[1] || "";
  function post(path, opts) { return fetch("/eyewitness/" + token + path, opts); }

  function checkImage(file, done) {
    var img = new Image();
    img.onload = function () {
      var c = document.createElement("canvas");
      var scale = Math.min(1, 512 / Math.max(img.width, img.height));
      c.width = Math.max(1, Math.round(img.width * scale));
      c.height = Math.max(1, Math.round(img.height * scale));
      var ctx = c.getContext("2d");
      ctx.drawImage(img, 0, 0, c.width, c.height);
      var d = ctx.getImageData(0, 0, c.width, c.height).data;
      var sum = 0, n = 0;
      for (var i = 0; i < d.length; i += 4) { sum += (d[i] + d[i + 1] + d[i + 2]) / 3; n++; }
      var bright = n ? sum / n : 0;
      if (bright < 25) { done("Too dark. Turn on a light or the torch, then try again."); return; }
      done(null);
    };
    img.onerror = function () { done(null); };
    var reader = new FileReader();
    reader.onload = function () { img.src = reader.result; };
    reader.onerror = function () { done(null); };
    reader.readAsDataURL(file);
  }

  document.querySelectorAll("[data-item-id]").forEach(function (li) {
    var itemId = li.getAttribute("data-item-id");
    var status = li.querySelector("[data-status]");
    var input = li.querySelector("[data-capture]");
    if (input) input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) return;
      status.textContent = "Checking…";
      var send = function () {
        var fd = new FormData();
        fd.append("item_id", itemId);
        fd.append("file", file);
        post("/upload", { method: "POST", body: fd }).then(function (r) { return r.json(); })
          .then(function (body) {
            if (body.error) { status.textContent = body.error; return; }
            var poll = setInterval(function () {
              post("/capture/" + body.capture_id).then(function (r) { return r.json(); })
                .then(function (s) {
                  if (s.status === "ok") { clearInterval(poll); status.textContent = "Got it, thank you."; }
                  else if (s.status === "retake") { clearInterval(poll); status.textContent = "Please retake: " + (s.advice || ""); }
                  else if (s.status === "failed") { clearInterval(poll); status.textContent = "That did not work. Please try again."; }
                });
            }, 2000);
          });
      };
      if (file.type.indexOf("image/") === 0) {
        checkImage(file, function (warn) {
          if (warn) { status.textContent = warn; return; }
          status.textContent = "Sending…"; send();
        });
      } else { status.textContent = "Sending…"; send(); }
    });
    var skip = li.querySelector("[data-skip]");
    if (skip) skip.addEventListener("click", function () {
      post("/skip", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_id: itemId, reason: "can't do this" }) })
        .then(function () {
          status.textContent = "Skipped.";
          var items = document.querySelectorAll("[data-item-id]");
          var statuses = document.querySelectorAll("[data-status]");
          var allSkipped = Array.prototype.every.call(statuses, function (s) { return s.textContent === "Skipped."; });
          if (allSkipped && items.length) {
            var offer = document.querySelector("[data-voice-offer]");
            if (offer) offer.hidden = false;
          }
        });
    });
  });

  var accept = document.querySelector("[data-voice-accept]");
  if (accept) accept.addEventListener("click", function () {
    post("/voice-offer", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept: true }) });
    document.querySelector("[data-voice-offer]").hidden = true;
  });
  var decline = document.querySelector("[data-voice-decline]");
  if (decline) decline.addEventListener("click", function () {
    post("/voice-offer", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept: false }) });
    document.querySelector("[data-voice-offer]").hidden = true;
  });
  var doneBtn = document.querySelector("[data-done]");
  if (doneBtn) doneBtn.addEventListener("click", function () {
    post("/done", { method: "POST" }).then(function (r) { return r.json(); })
      .then(function (b) {
        document.querySelector("[data-done-msg]").textContent = b.stays_open
          ? "Thank you. We'll keep this open for 7 days. Come back to this link when it happens again."
          : "Thank you, that's everything. You can close this page.";
      });
  });
})();
