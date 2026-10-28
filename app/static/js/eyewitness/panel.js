// Technician panel: load the Eyewitness fragment for this ticket and
// bind its manual-request form. The fragment itself is server-rendered.
(function () {
  var mount = document.querySelector("[data-ew-mount]");
  if (!mount) return;
  var ticket = mount.getAttribute("data-ticket");
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
        }).catch(function () { msg.textContent = "Could not create the request."; });
      });
    })
    .catch(function () { /* panel is additive; the ticket page stands alone */ });
})();
