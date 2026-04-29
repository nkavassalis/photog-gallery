(function () {
  const grid = document.querySelector(".photo-grid")
  const lb = document.getElementById("lightbox")
  if (!grid || !lb) return

  const items = Array.from(grid.querySelectorAll(".photo-grid-item"))
  if (items.length === 0) return

  const imgEl = lb.querySelector(".lb-img")
  const captionEl = lb.querySelector(".lb-caption")
  const permalinkEl = lb.querySelector(".lb-permalink")
  const prevBtn = lb.querySelector(".lb-prev")
  const nextBtn = lb.querySelector(".lb-next")
  const closeBtn = lb.querySelector(".lb-close")

  let currentIndex = -1

  function open(i) {
    currentIndex = i
    const el = items[i]
    imgEl.src = el.dataset.full
    imgEl.alt = el.dataset.caption || ""
    const caption = el.dataset.caption || ""
    captionEl.textContent = caption
    captionEl.hidden = !caption
    permalinkEl.href = el.getAttribute("href")
    prevBtn.disabled = i === 0
    nextBtn.disabled = i === items.length - 1
    lb.hidden = false
    document.body.classList.add("lightbox-open")
  }

  function close() {
    lb.hidden = true
    imgEl.src = ""
    document.body.classList.remove("lightbox-open")
    currentIndex = -1
  }

  function step(delta) {
    const next = currentIndex + delta
    if (next < 0 || next >= items.length) return
    open(next)
  }

  items.forEach((el, i) => {
    el.addEventListener("click", (e) => {
      // Allow modifier-clicks to open the per-photo permalink in a new tab.
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return
      e.preventDefault()
      open(i)
    })
  })

  closeBtn.addEventListener("click", close)
  prevBtn.addEventListener("click", () => step(-1))
  nextBtn.addEventListener("click", () => step(1))
  lb.addEventListener("click", (e) => {
    if (e.target === lb) close()
  })
  document.addEventListener("keydown", (e) => {
    if (lb.hidden) return
    if (e.key === "Escape") close()
    else if (e.key === "ArrowLeft") step(-1)
    else if (e.key === "ArrowRight") step(1)
  })
})()
