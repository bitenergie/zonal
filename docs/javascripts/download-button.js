// Adds a "Download" button to the header; it opens the latest GitHub release (the zonal-<version>.zip is attached there).
(function () {
  function add() {
    var inner = document.querySelector(".md-header__inner");
    if (!inner || inner.querySelector(".osm-download")) return;
    var a = document.createElement("a");
    a.className = "osm-download";
    a.href = "https://github.com/bitenergie/zonal/releases/latest";
    a.title = "Download the latest release of the Zonal add-on";
    a.innerHTML =
      '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M5 20h14v-2H5v2zM19 9h-4V3H9v6H5l7 7 7-7z"/></svg><span>Download</span>';
    var anchor = inner.querySelector(".md-search") || inner.querySelector("[for=__search]");
    inner.insertBefore(a, anchor);
  }
  if (document.readyState !== "loading") add();
  else document.addEventListener("DOMContentLoaded", add);
})();
