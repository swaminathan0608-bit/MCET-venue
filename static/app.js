// MCET Smart Hall & Venue — small browser helpers (no libraries needed)

// 1) Availability grid: click an empty part of a row to request that slot.
document.querySelectorAll(".avail").forEach((grid) => {
  const kind = grid.dataset.kind;
  if (!kind) return;
  const start = Number(grid.dataset.start);
  const end = Number(grid.dataset.end);
  const date = grid.dataset.date;
  const toMin = (t) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
  grid.querySelectorAll(".vrow .track").forEach((track) => {
    track.addEventListener("click", (e) => {
      if (e.target.closest(".slot")) return; // clicked an existing booking
      const r = track.getBoundingClientRect();
      const raw = start + ((e.clientX - r.left) / r.width) * (end - start);
      const snapped = Math.floor(raw / 30) * 30;
      if (snapped < toMin(track.dataset.open) || snapped >= toMin(track.dataset.close)) return; // closed hours
      const hh = String(Math.floor(snapped / 60)).padStart(2, "0");
      const mm = String(snapped % 60).padStart(2, "0");
      const qs = new URLSearchParams({ venue: track.dataset.venue, date, start: `${hh}:${mm}`, kind });
      location.href = `/request?${qs}`;
    });
  });
});

// 2) Request form: live availability check while the user types.
const form = document.getElementById("bookingForm");
if (form && typeof CHECK_URL !== "undefined") {
  const $ = (id) => document.getElementById(id);
  const verdict = $("verdict");
  const submit = $("submitBtn");
  const directRow = $("direct-row");
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  let timer;

  async function check() {
    const qs = new URLSearchParams({
      venue_id: $("f-venue").value,
      date: $("f-date").value,
      start: $("f-start").value,
      end: $("f-end").value,
      attendees: $("f-att") ? $("f-att").value : "1",
      kind: form.dataset.kind,
    });
    let data;
    try {
      data = await (await fetch(`${CHECK_URL}?${qs}`)).json();
    } catch {
      verdict.className = "verdict warn";
      verdict.textContent = "Could not check availability right now — you can still submit; the server will verify.";
      return;
    }
    $("f-facilities").textContent = data.facilities ? data.facilities.replaceAll(", ", " · ") : "";
    if (directRow) directRow.style.display = data.can_manage ? "flex" : "none";

    let html, cls;
    if (data.clashes.length) {
      cls = "bad";
      html = `⚠ <b>Not available.</b> ${data.clashes.map(esc).join("; ")}`;
    } else if (data.problems.length) {
      cls = "bad";
      html = `⚠ ${data.problems.map(esc).join(" ")}`;
    } else if (data.pending) {
      cls = "warn";
      html = `🕒 Free, but ${data.pending} other pending request${data.pending > 1 ? "s overlap" : " overlaps"} this slot.
              The supervisor will allot it to one request; the rest are declined automatically.`;
    } else {
      cls = "ok";
      html = `✔ <b>Available.</b> ${esc(data.venue)} is free for this time.`;
    }
    if (data.alternatives.length && cls !== "ok") {
      html += `<div class="mt-2 xs muted">Free at the same time and big enough:</div><div class="row mt-1" style="gap:.35rem">` +
        data.alternatives
          .map((a) => `<button type="button" class="chip" data-v="${a.id}">${esc(a.name)} · ${a.capacity}</button>`)
          .join("") + "</div>";
    }
    verdict.className = `verdict ${cls}`;
    verdict.innerHTML = html;
    submit.disabled = cls === "bad";
    verdict.querySelectorAll(".chip").forEach((c) =>
      c.addEventListener("click", () => {
        $("f-venue").value = c.dataset.v;
        check();
      }),
    );
  }

  ["f-venue", "f-date", "f-start", "f-end", "f-att"].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(check, 250);
    });
    el.addEventListener("change", check);
  });
  check();
}
