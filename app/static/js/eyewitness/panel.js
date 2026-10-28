// Technician panel: load the Eyewitness fragment for this ticket and
// bind its manual-request form and per-request actions. The fragment
// itself is server-rendered.
(function () {
  var mount = document.querySelector("[data-ew-mount]");
  if (!mount) return;
  var ticket = mount.getAttribute("data-ticket");
  function csrf(form) {
    var el = form ? form.querySelector("[name=csrf_token]") : null;
    return el ? el.value : "";
  }
  function bindActions(root) {
    [["data-ew-approve", "approve"], ["data-ew-cancel", "cancel"],
     ["data-ew-release", "release"]].forEach(function (pair) {
      root.querySelectorAll("[" + pair[0] + "]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var reqId = btn.getAttribute(pair[0]);
          var form = root.querySelector("[data-ew-form]");
          var body = new FormData();
          body.append("csrf_token", csrf(form));
          fetch("/helpdesk/tickets/" + ticket + "/evidence/" + reqId + "/" + pair[1], {
            method: "POST", body: body, credentials: "same-origin"
          }).then(function (r) { return r.json(); }).then(function () {
            load();
          }).catch(function () { /* the panel reload shows the state */ });
        });
      });
    });
  }
  function load() {
    fetch("/helpdesk/tickets/" + ticket + "/evidence", { credentials: "same-origin" })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        mount.innerHTML = html;
        var form = mount.querySelector("[data-ew-form]");
        if (!form) return;
        var msg = mount.querySelector("[data-ew-msg]");
        form.addEventListener("submit", function (ev) {
          ev.preventDefault();
          fetch("/helpdesk/tickets/" + ticket + "/evidence/request", {
            method: "POST", body: new FormData(form), credentials: "same-origin"
          }).then(function (r) { return r.json(); }).then(function (body) {
            if (body.error) { msg.textContent = body.error; return; }
            msg.textContent = body.status === "needs_approval"
              ? "Created, waiting for approval: " + body.link
              : "Request sent: " + body.link;
            load();
          }).catch(function () { msg.textContent = "Could not create the request."; });
        });
        bindActions(mount);
      })
      .catch(function () { /* panel is additive; the ticket page stands alone */ });
  }
  load();
})();
