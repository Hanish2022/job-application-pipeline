/* Plain-language layer for the vendored Cold-email page (vendor/yc-outreach/index.html, which we never edit).
   It only rewrites labels, adds explanations and a live preview, and tucks away the advanced parts. All the real
   behaviour (loading founders, filling templates, copy buttons, localStorage) is still the upstream code; we reuse its
   globals (tpl, DATA, fill, store, KEY, DEFAULTS) rather than copying its logic. Every step is guarded: if upstream changes
   shape, the page simply keeps working with its original wording.

   NOTE: "Expand all" is deliberately left alone, upstream decides open/close by comparing that button's text. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const guard = (name, fn) => { try { fn(); } catch (e) { console.warn(`cold-email overlay (${name}):`, e); } };
  const el = (tag, attrs = {}, ...kids) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "text") node.textContent = v; else node.setAttribute(k, v === true ? "" : v);
    }
    kids.flat().forEach((c) => node.append(c.nodeType ? c : document.createTextNode(String(c))));
    return node;
  };

  // The user never edits a template. They answer two plain questions and we build the template behind the scenes
  // (it is still stored in upstream's own tpl.subject / tpl.body, so upstream fills it in per company as usual).
  const GOALS = {
    internship: { label: "An internship", subject: "Internship at {company}?",
      ask: "I'm looking for an internship and would love to know if {company} has any openings." },
    job: { label: "A full-time job", subject: "Interested in a role at {company}",
      ask: "I'm looking for a full-time role and would love to know if {company} is hiring." },
    chat: { label: "Just a short chat", subject: "Quick chat about {company}?",
      ask: "I'd love a short chat to learn more about what you're building at {company}." },
  };
  const SIMPLE_KEY = "oc-simple-v1";
  function buildMessage(about, goal) {
    const g = GOALS[goal] || GOALS.internship;
    const body = [
      "Hi {first_name},",
      "I came across {company} — {one_liner}.",
      (about || "").trim(),                                                   // omitted entirely when empty
      g.ask,
      "Thanks,\n{my_name}\nPortfolio: {portfolio}\nGitHub: {github}\nResume: {resume}",
    ].filter(Boolean).join("\n\n");
    return { subject: g.subject, body };
  }
  // The starter text an earlier version of this page installed; treated like upstream's default (safe to replace).
  const OLD_STARTER_SUBJECT = "Interested in working at {company}";
  const OLD_STARTER_BODY_START = "Hi {first_name},\n\nI came across {company} — {one_liner}. It looks like a great team";
  const BRACKET = /\[[^\]\n]+\]/;
  const SAMPLE = {
    name: "Acme", slug: "acme", batch: "Winter 2024", one_liner: "Builds rockets for small teams",
    founders: [{ name: "Jane Doe", title: "CEO", emails_found: [], email_guesses: [] }],
  };
  const KIND_HINT = {
    site: "This address is published on the company's own website, so it's the most reliable.",
    valid: "This address was checked by Apify and looks valid.",
    unknown: "Apify found this address but couldn't confirm the mailbox. Usually right.",
    guess: "This address is a guess built from the founder's name. It's right about 80% of the time, so it may bounce. Checking their LinkedIn first can help.",
    none: "No address found. Use the LinkedIn / X links above to reach them.",
  };
  const BADGE_TEXT = { "on site": "on website", guess: "guessed", "no email": "no email found" };

  const upstream = () => typeof tpl !== "undefined" && typeof fill === "function" && typeof DATA !== "undefined";

  /* ------------------------------------------------------------------ intro + step 1 */
  function intro() {
    const sub = $("p.sub");
    sub.replaceChildren(
      el("strong", { text: "How it works" }),
      el("ol", { class: "oc-steps" },
        el("li", { text: "Add your name and write a short message about you, once (steps 1 and 2)." }),
        el("li", { text: "Pick a YC batch and load its companies (step 3)." }),
        el("li", { text: "Open a company, check the address, then copy the email or open it in your email app." }),
      ),
      el("span", { class: "oc-strong", text: "Nothing is sent automatically. You review and send every email yourself." }),
    );
  }

  function setLabelText(input, text) {
    const label = input && input.closest("label");
    const node = label && Array.from(label.childNodes).find((n) => n.nodeType === Node.TEXT_NODE);
    if (node) node.nodeValue = text;
  }

  function aboutYou() {
    const h = $$("h2").find((x) => x.textContent.trim().startsWith("1."));
    h.textContent = "1. Your name and links ";
    h.append(el("span", { class: "oc-optional", text: "(all optional)" }));
    h.after(el("p", { class: "oc-note",
      text: "This becomes a short signature at the end of your email. Leave any field empty and that line is simply left out. Links only: nothing is uploaded or attached." }));
    setLabelText($('[data-k="my_name"]'), "Your name");
    setLabelText($('[data-k="portfolio"]'), "Portfolio or website (optional)");
    setLabelText($('[data-k="github"]'), "GitHub profile (optional)");
    setLabelText($('[data-k="resume"]'), "Link to your resume (optional)");
    $('[data-k="resume"]').placeholder = "Drive / Notion / Dropbox link";
    // Use the name from your profile on the Jobs page, unless you've already typed one.
    fetch("/api/profile").then((r) => (r.ok ? r.json() : null)).then((p) => {
      const input = $('[data-k="my_name"]');
      if (p && p.name && input && !input.value.trim()) { input.value = p.name; input.dispatchEvent(new Event("input")); }
    }).catch(() => {});
  }

  /* ------------------------------------------------------------------ step 2 */
  function fieldValues(subject, body) {
    for (const [k, v] of [["subject", subject], ["body", body]]) {
      const input = $(`[data-k="${k}"]`);
      input.value = v;
      input.dispatchEvent(new Event("input"));       // upstream's own handler saves it and refreshes open drafts
    }
  }

  function yourMessage() {
    const h = $$("h2").find((x) => x.textContent.trim().startsWith("2."));
    h.textContent = "2. Your message";
    h.after(el("p", { class: "oc-note",
      text: "Just tell them a little about you. We add the greeting, the company and what it does, and your signature for you." }));

    // Everything upstream showed for the template moves into a collapsed "Advanced" box.
    const subjectLabel = $('[data-k="subject"]').closest("label");
    const bodyLabel = $('[data-k="body"]').closest("label");
    setLabelText($('[data-k="subject"]'), "Subject line");
    setLabelText($('[data-k="body"]'), "Message");
    [$("#vars"), $("#vars") && $("#vars").nextElementSibling].forEach((n) => { if (n) n.hidden = true; });

    const saved = (() => { try { return JSON.parse(localStorage.getItem(SIMPLE_KEY)) || {}; } catch { return {}; } })();
    const about = el("textarea", { id: "oc-about", rows: "3", maxlength: "600",
      placeholder: "e.g. I'm a final-year CS student. I built a delivery app with React, Node and MongoDB, and I'm looking for an SDE internship." });
    about.value = saved.about || "";
    const goal = el("select", { id: "oc-goal" }, ...Object.entries(GOALS).map(([k, g]) => el("option", { value: k, text: g.label })));
    goal.value = GOALS[saved.goal] ? saved.goal : "internship";
    const nudge = el("p", { class: "oc-nudge", id: "oc-about-nudge", role: "status", hidden: true,
      text: "Tip: add a sentence about yourself, otherwise the email feels generic." });
    const aboutLabel = el("label", { class: "f" }, "A little about you (1–2 sentences)", about);
    const goalLabel = el("label", { class: "f" }, "What are you looking for?", goal);

    const customNote = el("p", { class: "oc-warn", id: "oc-custom-note", role: "status", hidden: true,
      text: "You've written your own full email text (see Advanced below). Changing the boxes here will replace it." });
    // NOTE: not a <details>: upstream's filter()/update() loop over every <details> on the page and expect company data on each.
    const ADV_CLOSED = "Advanced: edit the full email text", ADV_OPEN = "Hide the full email text";
    const advPanel = el("div", { id: "oc-advanced-panel", class: "oc-advanced-panel", hidden: true },
      el("p", { class: "oc-note",
        text: "Words in {curly braces} are swapped for each company: {first_name} {company} {one_liner} {batch} {my_name}. Changing the two boxes above rebuilds this text and replaces anything you typed here." }),
      subjectLabel, bodyLabel);
    const advanced = el("button", { type: "button", class: "oc-toggle", id: "oc-advanced", "aria-expanded": "false",
      "aria-controls": "oc-advanced-panel", text: ADV_CLOSED });
    const setAdvanced = (open) => {
      advPanel.hidden = !open;
      advanced.setAttribute("aria-expanded", String(open));
      advanced.textContent = open ? ADV_OPEN : ADV_CLOSED;
    };
    advanced.addEventListener("click", () => setAdvanced(advPanel.hidden));
    const warn = el("p", { class: "oc-warn", id: "oc-bracket-warning", role: "status", hidden: true,
      text: "Your message still has [bracketed] text. Replace it with your own sentences before you send anything." });
    const preview = el("div", { class: "oc-preview" },
      el("div", { class: "oc-preview__head" }, "This is what the email will look like ", el("span", { id: "oc-preview-for" })),
      el("div", { class: "oc-preview__subject", id: "oc-preview-subject" }),
      el("pre", { class: "oc-preview__body", id: "oc-preview-body" }));
    const host = $("#vars") ? $("#vars").parentElement : h.parentElement;
    host.append(el("div", { class: "oc-two" }, aboutLabel, goalLabel), nudge, customNote, warn, preview,
                el("p", { class: "oc-toggle-row" }, advanced), advPanel);

    const state = () => ({ about: about.value, goal: goal.value });
    const matchesGenerated = () => { const m = buildMessage(saved.about, saved.goal); return tpl.subject === m.subject && tpl.body === m.body; };
    const isDefaultish = () =>
      (tpl.body === DEFAULTS.body && tpl.subject === DEFAULTS.subject) ||
      (tpl.subject === OLD_STARTER_SUBJECT && tpl.body.startsWith(OLD_STARTER_BODY_START));
    const generate = () => {
      const { about: a, goal: g } = state();
      try { localStorage.setItem(SIMPLE_KEY, JSON.stringify({ about: a, goal: g })); } catch { /* private mode: fine */ }
      const m = buildMessage(a, g);
      fieldValues(m.subject, m.body);
      nudge.hidden = !!a.trim();
      customNote.hidden = true;
    };
    const onSimpleEdit = () => { generate(); };
    about.addEventListener("input", onSimpleEdit);
    goal.addEventListener("change", onSimpleEdit);

    for (const k of ["subject", "body", "my_name", "portfolio", "github", "resume"]) {
      $(`[data-k="${k}"]`).addEventListener("input", (e) => {
        refreshPreview(); refreshBrackets();
        if ((k === "subject" || k === "body") && e.isTrusted) customNote.hidden = false;   // typed by hand in Advanced
      });
    }
    if (matchesGenerated() || isDefaultish()) { generate(); }
    else { customNote.hidden = false; setAdvanced(true); nudge.hidden = true; }
    refreshPreview(); refreshBrackets();
  }

  function refreshPreview() {
    if (!upstream() || !$("#oc-preview-body")) return;
    const company = (DATA || []).find((c) => c.founders && c.founders.length) || SAMPLE;
    $("#oc-preview-for").textContent = company === SAMPLE ? "(example company)" : `(for ${company.name})`;
    $("#oc-preview-subject").textContent = `Subject: ${fill(tpl.subject, company)}`;
    $("#oc-preview-body").textContent = fill(tpl.body, company);
  }

  function refreshBrackets() {
    if (!upstream()) return;
    const stillHas = BRACKET.test(tpl.body) || BRACKET.test(tpl.subject);
    const w = $("#oc-bracket-warning");
    if (w) w.hidden = !stillHas;
    $$(".oc-draft-warn").forEach((n) => { n.hidden = !stillHas; });
  }

  /* ------------------------------------------------------------------ steps 3 and 4 */
  function tuckAwayVerifiedEmails() {
    const h = $$("h2").find((x) => x.textContent.trim().startsWith("4."));
    const panel = h && h.closest(".panel");
    if (!panel) return;
    panel.id = "oc-verified"; panel.hidden = true;
    const toggle = el("button", { type: "button", class: "oc-toggle", "aria-expanded": "false", "aria-controls": "oc-verified",
      text: "Advanced: find verified emails (optional, needs an Apify account)" });
    toggle.addEventListener("click", () => {
      const open = panel.hidden;
      panel.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = open ? "Hide verified-email options" : "Advanced: find verified emails (optional, needs an Apify account)";
    });
    panel.before(el("p", { class: "oc-toggle-row" }, toggle));
  }

  function toolbar() {
    const names = { "": "Any email type", valid: "Verified", unknown: "Unverified", site: "Found on the company's website",
                    guess: "Guessed from the founder's name", none: "No email found" };
    $$("#st option").forEach((o) => { if (o.value in names) o.textContent = names[o.value]; });
    $("#all").title = "Open or close every company below";
    const shown = $("#shown");
    if (shown) shown.after(el("p", { class: "oc-legend" },
      el("strong", { text: "Email types: " }),
      "found on website = the company publishes it (most reliable) · guessed = built from the founder's name, may bounce · verified = checked with Apify."));
  }

  function decorate(d) {
    if (d.dataset.oc) return;
    d.dataset.oc = "1";
    $$(".badge", d).forEach((b) => { const t = b.textContent.trim(); if (BADGE_TEXT[t]) b.textContent = BADGE_TEXT[t]; });
    const body = $(".body", d);
    if (!body) return;
    $$(".r", body).forEach((row) => {
      const label = $("b", row);
      if (!label) return;
      const t = label.textContent.trim();
      if (t === "To") {
        label.textContent = "Send to";
        const kind = (Array.from($(".badge", row)?.classList || []).find((c) => c in KIND_HINT)) || "none";
        row.after(el("p", { class: "oc-hint", text: KIND_HINT[kind] }));
      } else if (t === "Also") {
        label.textContent = "Also there";
        row.title = "Another founder at this company. This draft is written to the 'Send to' person.";
      }
    });
    const pre = $("pre", body);
    if (pre) pre.before(el("p", { class: "oc-draft-head", text: "Your email" }),
                        el("p", { class: "oc-warn oc-draft-warn", role: "status", hidden: true,
                                  text: "This email still has [bracketed] text. Replace it in step 2 before sending." }));
    const names = { body: "Copy message", to: "Copy address", subject: "Copy subject" };
    $$("button[data-a]", body).forEach((b) => { if (names[b.dataset.a]) b.textContent = names[b.dataset.a]; });
    const mail = $("a.mail", body); if (mail) mail.textContent = "Open in email app";
    const sent = $('input[data-a="sent"]', body);
    const sentLabel = sent && sent.closest("label");
    const textNode = sentLabel && Array.from(sentLabel.childNodes).find((n) => n.nodeType === Node.TEXT_NODE);
    if (textNode) textNode.nodeValue = " Mark as sent";
    refreshBrackets();
  }

  function watchList() {
    const list = $("#list");
    const run = () => { $$("details", list).forEach((d) => guard("decorate", () => decorate(d))); refreshPreview(); };
    new MutationObserver(run).observe(list, { childList: true });     // upstream rebuilds the list with innerHTML
    run();
  }

  /* ------------------------------------------------------------------ go */
  if (!upstream()) { console.warn("cold-email overlay: upstream page not recognised; leaving it as-is"); return; }
  guard("intro", intro);
  guard("about you", aboutYou);
  guard("message", yourMessage);
  guard("verified emails", tuckAwayVerifiedEmails);
  guard("toolbar", toolbar);
  guard("list", watchList);
})();
