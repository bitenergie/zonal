// GLightbox: every content image opens full size on click (also after instant navigation).
(function () {
  var lb = null;
  function setup() {
    if (lb) { lb.destroy(); lb = null; }
    document.querySelectorAll(".md-typeset img").forEach(function (img) {
      if (img.closest("a.glightbox") || img.closest(".twemoji, .emojione")) return;
      var a = document.createElement("a");
      a.className = "glightbox";
      a.href = img.currentSrc || img.src;
      a.setAttribute("data-type", "image");
      if (img.alt) a.setAttribute("data-description", img.alt);
      img.parentNode.insertBefore(a, img);
      a.appendChild(img);
    });
    lb = GLightbox({ selector: ".glightbox", touchNavigation: true, zoomable: true });
  }
  if (typeof document$ !== "undefined") document$.subscribe(setup);
  else if (document.readyState !== "loading") setup();
  else document.addEventListener("DOMContentLoaded", setup);
})();
