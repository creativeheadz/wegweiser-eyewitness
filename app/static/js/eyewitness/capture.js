// Eyewitness customer capture: live camera where the phone allows
// it, an instant check that holds the same numbers as the server,
// upload, then poll for the server verdict. No Meta key ever
// reaches the browser. Canvas capture drops EXIF and GPS data.
(function () {
  var token = location.pathname.split("/").filter(Boolean)[1] || "";
  function post(path, opts) { return fetch("/eyewitness/" + token + path, opts); }

  // Stepper state. The server marks finished items with data-done
  // on a reload. With script off the plain list stays in view.
  var itemEls = Array.prototype.slice.call(
    document.querySelectorAll("[data-item-id]"));
  var currentStep = 0;
  var stepperReady = false;
  function elIsDone(li) {
    return !!(li && li.getAttribute("data-done") === "true");
  }
  function firstUnfinishedFrom(start) {
    var i;
    for (i = Math.max(0, start || 0); i < itemEls.length; i++) {
      if (!elIsDone(itemEls[i])) return i;
    }
    for (i = 0; i < Math.min(start || 0, itemEls.length); i++) {
      if (!elIsDone(itemEls[i])) return i;
    }
    return itemEls.length ? itemEls.length - 1 : 0;
  }
  function showStep(i) {
    if (!itemEls.length) return;
    currentStep = Math.max(0, Math.min(itemEls.length - 1, i));
    itemEls.forEach(function (li, n) { li.hidden = n !== currentStep; });
    var progress = document.querySelector("[data-progress]");
    var nav = document.querySelector("[data-step-nav]");
    if (progress) {
      progress.textContent = "Step " + (currentStep + 1) + " of " + itemEls.length;
      progress.hidden = false;
    }
    if (nav) {
      nav.hidden = false;
      var backBtn = nav.querySelector("[data-nav-back]");
      var nextBtn = nav.querySelector("[data-nav-next]");
      if (backBtn) backBtn.disabled = currentStep === 0;
      if (nextBtn) nextBtn.disabled = currentStep === itemEls.length - 1;
    }
    var heading = itemEls[currentStep].querySelector("h2");
    if (heading) {
      if (!heading.getAttribute("tabindex")) heading.setAttribute("tabindex", "-1");
      heading.focus();
    }
  }
  // "Got it" stays in view for a moment before the next step shows, so
  // the customer sees each item accepted.
  var ADVANCE_DELAY_MS = 1500;
  function markDoneAndAdvance(li) {
    if (li) li.setAttribute("data-done", "true");
    if (!stepperReady || !itemEls.length) return;
    var anyUnfinished = itemEls.some(function (el) { return !elIsDone(el); });
    if (!anyUnfinished) return;
    var idx = li ? itemEls.indexOf(li) : currentStep;
    if (idx < 0) idx = currentStep;
    setTimeout(function () {
      // The customer may have moved to another step by hand meanwhile.
      if (currentStep !== idx) return;
      showStep(firstUnfinishedFrom(idx + 1));
    }, ADVANCE_DELAY_MS);
  }

  // Numbers held with the server readers under one test:
  // quality.BRIGHT_MIN / HIGHLIGHT_MIN, video MIN_DURATION_S and
  // voice MIN_SECONDS / MAX_SECONDS / MIN_RMS.
  var VIDEO_MIN_SECONDS = 3;
  var VOICE_MIN_SECONDS = 1;
  var VOICE_MAX_SECONDS = 60;
  var VOICE_MIN_RMS = 0.008;
  var TOO_SHORT_VIDEO = "The clip is too short. Film the light for the full time shown on the item, then try again.";
  var TOO_SHORT_VOICE = "The voice note is too short. Record again and speak for a few seconds.";
  var TOO_QUIET_VOICE = "The voice note is too quiet to hear. Move the phone closer, then try again.";

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
      // The server's rule (readers/quality.py): grey as Pillow computes it,
      // and too dark only when the brightest 0.1 percent is dim as well, so
      // the black crash screen gets through on its small bright text.
      var hist = [], sum = 0, n = 0, k;
      for (k = 0; k < 256; k++) hist[k] = 0;
      for (var i = 0; i < d.length; i += 4) {
        var g = Math.round(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
        hist[g]++; sum += g; n++;
      }
      var top = 255, seen = 0;
      while (top > 0 && seen + hist[top] < n * 0.001) { seen += hist[top]; top--; }
      var bright = n ? sum / n : 0;
      if (bright < 25 && top < 50) { done("It is too dark to see anything. Turn on a light, then try again."); return; }
      done(null);
    };
    img.onerror = function () { done(null); };
    var reader = new FileReader();
    reader.onload = function () { img.src = reader.result; };
    reader.onerror = function () { done(null); };
    reader.readAsDataURL(file);
  }

  var POLL_TIMEOUT_MS = 5 * 60 * 1000;
  var POLL_TIMEOUT_MESSAGE = "Checking is taking longer than usual. Please try again.";

  function uploadAndPoll(itemId, blob, filename, status, li) {
    status.textContent = "Sending…";
    var fd = new FormData();
    fd.append("item_id", itemId);
    fd.append("file", blob, filename);
    post("/upload", { method: "POST", body: fd }).then(function (r) { return r.json(); })
      .then(function (body) {
        if (body.error) { status.textContent = body.error; return; }
        var started = Date.now();
        var poll = setInterval(function () {
          if (Date.now() - started >= POLL_TIMEOUT_MS) {
            clearInterval(poll);
            status.textContent = POLL_TIMEOUT_MESSAGE;
            return;
          }
          post("/capture/" + body.capture_id).then(function (r) { return r.json(); })
            .then(function (s) {
              if (s.status === "ok") { clearInterval(poll); status.textContent = "Got it, thank you."; markDoneAndAdvance(li || null); }
              else if (s.status === "retake") { clearInterval(poll); status.textContent = "Please retake: " + (s.advice || ""); }
              else if (s.status === "failed") { clearInterval(poll); status.textContent = "That did not work. Please try again."; }
            });
        }, 2000);
      }).catch(function () { status.textContent = "That did not work. Please try again."; });
  }

  document.querySelectorAll("[data-item-id]").forEach(function (li) {
    var itemId = li.getAttribute("data-item-id");
    var kind = li.getAttribute("data-kind");
    var maxSeconds = parseInt(li.getAttribute("data-max-seconds") || "0", 10) || 0;
    var status = li.querySelector("[data-status]");
    var input = li.querySelector("[data-capture]");
    if (input) input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) return;
      status.textContent = "Checking…";
      if (file.type.indexOf("image/") === 0) {
        checkImage(file, function (warn) {
          if (warn) { status.textContent = warn; return; }
          uploadAndPoll(itemId, file, file.name || "photo.jpg", status, li);
        });
      } else { uploadAndPoll(itemId, file, file.name || "capture", status, li); }
    });

    // Live capture. Where the camera is refused or missing the file
    // input above stays the path, with the same checks after upload.
    var preview = li.querySelector("[data-preview]");
    var stream = null;
    function stopStream() {
      if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
      if (preview) { preview.hidden = true; preview.srcObject = null; }
    }
    function openCamera(wantAudio) {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        status.textContent = "No camera here. Use the file button below.";
        return Promise.reject(new Error("no camera"));
      }
      var want = kind === "voice" ? { audio: true }
        : { video: { facingMode: "environment" }, audio: !!wantAudio };
      return navigator.mediaDevices.getUserMedia(want).then(function (s) {
        stream = s;
        if (preview) { preview.srcObject = s; preview.hidden = false; preview.play(); }
        return s;
      }).catch(function () {
        status.textContent = "Camera not allowed. Use the file button below.";
        throw new Error("refused");
      });
    }

    var startCam = li.querySelector("[data-start-camera]");
    var takeBtn = li.querySelector("[data-take-photo]");
    if (startCam && kind === "photo") startCam.addEventListener("click", function () {
      openCamera(false).then(function () {
        startCam.hidden = true; if (takeBtn) takeBtn.hidden = false;
        status.textContent = "Hold the phone 30 cm away and tap Take photo.";
      }).catch(function () {});
    });
    if (takeBtn) takeBtn.addEventListener("click", function () {
      var canvas = li.querySelector("[data-canvas]");
      if (!preview || !canvas || !preview.videoWidth) return;
      var scale = Math.min(1, 3000 / Math.max(preview.videoWidth, preview.videoHeight));
      canvas.width = Math.round(preview.videoWidth * scale);
      canvas.height = Math.round(preview.videoHeight * scale);
      canvas.getContext("2d").drawImage(preview, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(function (blob) {
        if (!blob) return;
        checkImage(blob, function (warn) {
          if (warn) { status.textContent = warn; return; }
          stopStream(); if (takeBtn) takeBtn.hidden = true; if (startCam) startCam.hidden = false;
          uploadAndPoll(itemId, blob, "photo.jpg", status, li);
        });
      }, "image/jpeg", 0.9);
    });

    var recVideo = li.querySelector("[data-record-video]");
    var stopVideo = li.querySelector("[data-stop-video]");
    var countdown = li.querySelector("[data-countdown]");
    var timer = li.querySelector("[data-timer]");
    if (startCam && kind === "video") startCam.addEventListener("click", function () {
      openCamera(true).then(function () {
        startCam.hidden = true; if (recVideo) recVideo.hidden = false;
        status.textContent = "Frame the power light, then press Record video.";
      }).catch(function () {});
    });
    if (recVideo) recVideo.addEventListener("click", function () {
      if (!stream || typeof MediaRecorder === "undefined") {
        status.textContent = "Recording is not available here. Use the file button below.";
        return;
      }
      var step = 3;
      recVideo.hidden = true;
      if (countdown) countdown.textContent = "Get ready: " + step;
      var tick = setInterval(function () {
        step -= 1;
        if (step > 0) { if (countdown) countdown.textContent = "Get ready: " + step; return; }
        clearInterval(tick);
        if (countdown) countdown.textContent = "";
        var chunks = [];
        var mime = MediaRecorder.isTypeSupported("video/webm") ? "video/webm" : "";
        var rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
        var started = Date.now();
        var limit = (maxSeconds || 15) * 1000;
        if (stopVideo) stopVideo.hidden = false;
        var clock = setInterval(function () {
          var left = Math.max(0, Math.ceil((limit - (Date.now() - started)) / 1000));
          if (timer) timer.textContent = "Recording: " + left + " seconds left";
          if (Date.now() - started >= limit && rec.state !== "inactive") rec.stop();
        }, 250);
        rec.ondataavailable = function (ev) { if (ev.data && ev.data.size) chunks.push(ev.data); };
        rec.onstop = function () {
          clearInterval(clock);
          if (timer) timer.textContent = "";
          if (stopVideo) stopVideo.hidden = true;
          var seconds = (Date.now() - started) / 1000;
          var type = rec.mimeType || "video/webm";
          var blob = new Blob(chunks, { type: type });
          stopStream(); if (startCam) startCam.hidden = false;
          if (seconds < VIDEO_MIN_SECONDS) { status.textContent = TOO_SHORT_VIDEO; if (recVideo) recVideo.hidden = false; return; }
          uploadAndPoll(itemId, blob, type.indexOf("mp4") >= 0 ? "video.mp4" : "video.webm", status, li);
        };
        if (stopVideo) stopVideo.onclick = function () { if (rec.state !== "inactive") rec.stop(); };
        rec.start(500);
      }, 1000);
    });

    var recVoice = li.querySelector("[data-record-voice]");
    var stopVoice = li.querySelector("[data-stop-voice]");
    if (recVoice) recVoice.addEventListener("click", function () {
      openCamera(false).then(function () {
        if (typeof MediaRecorder === "undefined") {
          status.textContent = "Recording is not available here. Use the file button below.";
          stopStream(); return;
        }
        var chunks = [];
        var rec = new MediaRecorder(stream);
        var started = Date.now();
        var limit = (maxSeconds || VOICE_MAX_SECONDS) * 1000;
        recVoice.hidden = true; if (stopVoice) stopVoice.hidden = false;
        // Level check held with the server: the mean level over the
        // note must reach VOICE_MIN_RMS or the server asks again.
        var actx = null, peakSum = 0, peakCount = 0, analyser = null;
        try {
          actx = new (window.AudioContext || window.webkitAudioContext)();
          analyser = actx.createAnalyser();
          actx.createMediaStreamSource(stream).connect(analyser);
        } catch (err) { analyser = null; }
        var clock = setInterval(function () {
          var left = Math.max(0, Math.ceil((limit - (Date.now() - started)) / 1000));
          if (timer) timer.textContent = "Recording: " + left + " seconds left";
          if (analyser) {
            var buf = new Float32Array(analyser.fftSize);
            analyser.getFloatTimeDomainData(buf);
            var sum = 0;
            for (var i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
            peakSum += Math.sqrt(sum / buf.length); peakCount += 1;
          }
          if (Date.now() - started >= limit && rec.state !== "inactive") rec.stop();
        }, 250);
        rec.ondataavailable = function (ev) { if (ev.data && ev.data.size) chunks.push(ev.data); };
        rec.onstop = function () {
          clearInterval(clock);
          if (actx) actx.close();
          if (timer) timer.textContent = "";
          if (stopVoice) stopVoice.hidden = true;
          if (recVoice) recVoice.hidden = false;
          var seconds = (Date.now() - started) / 1000;
          var blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
          stopStream();
          if (seconds < VOICE_MIN_SECONDS) { status.textContent = TOO_SHORT_VOICE; return; }
          if (peakCount && (peakSum / peakCount) < VOICE_MIN_RMS) { status.textContent = TOO_QUIET_VOICE; return; }
          var type = rec.mimeType || "";
          uploadAndPoll(itemId, blob, type.indexOf("mp4") >= 0 || type.indexOf("m4a") >= 0 ? "voice.m4a" : "voice.webm", status, li);
        };
        if (stopVoice) stopVoice.onclick = function () { if (rec.state !== "inactive") rec.stop(); };
        rec.start(500);
      }).catch(function () {});
    });

    li.querySelectorAll("[data-answer-opt]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        post("/answer", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ item_id: itemId, answer: btn.getAttribute("data-answer-opt") }) })
          .then(function () { status.textContent = "Got it, thank you."; markDoneAndAdvance(li); });
      });
    });
    var save = li.querySelector("[data-answer-save]");
    if (save) save.addEventListener("click", function () {
      var box = li.querySelector("[data-answer-text]");
      post("/answer", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_id: itemId, answer: box ? box.value : "" }) })
        .then(function () { status.textContent = "Got it, thank you."; markDoneAndAdvance(li); });
    });
    var skip = li.querySelector("[data-skip]");
    if (skip) skip.addEventListener("click", function () {
      post("/skip", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_id: itemId, reason: "can't do this" }) })
        .then(function () {
          status.textContent = "Skipped.";
          li.setAttribute("data-skipped", "true");
          markDoneAndAdvance(li);
          var allSkipped = itemEls.length && itemEls.every(function (el) {
            return el.getAttribute("data-skipped") === "true";
          });
          if (allSkipped) {
            var offer = document.querySelector("[data-voice-offer]");
            if (offer) offer.hidden = false;
          }
        });
    });
  });

  var accept = document.querySelector("[data-voice-accept]");
  if (accept) accept.addEventListener("click", function () {
    post("/voice-offer", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept: true }) }).then(function () { location.reload(); });
  });
  var decline = document.querySelector("[data-voice-decline]");
  if (decline) decline.addEventListener("click", function () {
    post("/voice-offer", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept: false }) });
    document.querySelector("[data-voice-offer]").hidden = true;
  });
  var doneBtn = document.querySelector(".ew-done");
  if (doneBtn) doneBtn.addEventListener("click", function () {
    post("/done", { method: "POST" }).then(function (r) { return r.json(); })
      .then(function (b) {
        var main = document.querySelector("[data-next-time-days]");
        var days = (main && main.getAttribute("data-next-time-days")) || "7";
        document.querySelector("[data-done-msg]").textContent = b.stays_open
          ? "Thank you. We'll keep this open for " + days + " days. Come back to this link when it happens again."
          : "Thank you, that's everything. You can close this page.";
      });
  });

  // One item at a time where script runs. The list stays as the
  // fallback with script off, so nothing is hidden in the markup.
  // On a reload the stepper starts at the first unfinished item.
  (function initStepper() {
    if (itemEls.length < 2) return;
    var nav = document.querySelector("[data-step-nav]");
    if (!nav || !document.querySelector("[data-progress]")) return;
    var backBtn = nav.querySelector("[data-nav-back]");
    var nextBtn = nav.querySelector("[data-nav-next]");
    if (backBtn) backBtn.addEventListener("click", function () { showStep(currentStep - 1); });
    if (nextBtn) nextBtn.addEventListener("click", function () { showStep(currentStep + 1); });
    stepperReady = true;
    showStep(firstUnfinishedFrom(0));
  })();
})();
