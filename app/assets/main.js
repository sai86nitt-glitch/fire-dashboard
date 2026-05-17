// Scroll-triggered fade-up animations via IntersectionObserver
(function () {
  var observer = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          var el = entry.target;
          var delay = parseInt(el.dataset.delay || "0", 10);
          setTimeout(function () {
            el.classList.add("visible");
          }, delay);
          observer.unobserve(el);
        }
      });
    },
    { threshold: 0.07, rootMargin: "0px 0px -32px 0px" }
  );

  function observe() {
    document.querySelectorAll(".fade-up:not(.visible)").forEach(function (el) {
      observer.observe(el);
    });
  }

  // Run on initial load
  document.addEventListener("DOMContentLoaded", observe);

  // Re-run after Dash renders new content (poll for DOM changes)
  var prevCount = 0;
  setInterval(function () {
    var count = document.querySelectorAll(".fade-up").length;
    if (count !== prevCount) {
      prevCount = count;
      observe();
    }
  }, 300);
})();
