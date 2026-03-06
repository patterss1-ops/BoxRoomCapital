(function () {
  var endpointInput = document.getElementById("endpoint");
  var saveButton = document.getElementById("save");
  var statusEl = document.getElementById("status");

  chrome.storage.sync.get({ endpoint: "" }, function (data) {
    endpointInput.value = data.endpoint || "";
  });

  saveButton.addEventListener("click", function () {
    var endpoint = String(endpointInput.value || "").trim().replace(/\/$/, "");
    chrome.storage.sync.set({ endpoint: endpoint }, function () {
      statusEl.textContent = "Saved.";
      setTimeout(function () {
        statusEl.textContent = "";
      }, 1500);
    });
  });
})();
