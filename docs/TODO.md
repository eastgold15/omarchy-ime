# Planned work

Things this plugin does not do yet, with enough detail to start from. Each entry
says what is already settled and what is still open, so picking one up does not
mean re-deriving what was already measured.

---

## LLM sentence translation

Translate the sentence still being composed and offer the translation as an
extra candidate, for the case where the writer thinks in Chinese and wants
English out.

**Status: feasibility proven, not designed, not built. Off by default when it
ships.**

### What it is not

Not a general translation tool. `ryuhzk.translation` already covers selected
text, files, tone and context; this is one action with one request.

Not per-candidate translation. Candidates already carry a CC-CEDICT gloss that
is instant, offline and accurate. An LLM asked to do the same job would be
slower by orders of magnitude and less correct.

Not translation of text already committed. An input method cannot read what the
application holds, and rewriting it by injecting backspaces breaks as soon as
the user moves the cursor. The only moment the whole sentence is reachable is
while Rime still owns it.

### Shape

Source text is the current full-sentence candidate's Chinese, not the pinyin:
Rime has already disambiguated it, the raw input has not.

    ni hao ma                          composing, nothing committed
      1. 你好吗
      2. ...
             hotkey
      1. How are you?                  selectable, commits as English
      2. 你好吗

### What the spike established

Measured against librime 1.17.0 and fcitx5 on this machine. Throwaway probes
lived in a scratch `RIME_USER_DIR`; nothing here was built from assumption.

**A candidate can appear with no further keystroke.** Not through a librime API
and not over D-Bus - both were tried and neither works. The mechanism is a
synthetic key bound to a Rime option toggle:

1. The schema binds a key nothing else uses to a dummy option:
   `{ when: always, accept: F24, toggle: llm_poke }`
2. The filter spawns the request and returns; the composition is untouched.
3. The child writes its answer and injects that key.
4. Rime consumes the key, the option toggle forces a recomposition, the filter
   re-runs, finds the answer, and yields the candidate.
5. fcitx5's `RimeState::keyEvent` calls `updateUI()`, which rebuilds and
   repaints the candidate window.

**Input stays responsive.** librime-lua opens the full standard library, so
`os.execute` with a detached child costs 0.8-1.1 ms and the child outlives the
librime process. With 25 outstanding 20-second requests, keystrokes stayed at
0.9-1.8 ms. Having the filter installed at all costs about 7 us per keystroke
when no answer is waiting.

**The keymap churn is harmless.** Injecting through `wtype` attaches and tears
down a virtual keyboard, which fcitx5 logs as a keymap update. Tested live in a
scratch window: a composition of `nihao` survived an injected key and still
committed `你好` intact. This was the one link that could have sunk the design.

**Latency is far lower than assumed.** LM Studio running `hunyuan-mt-7b`
answered a full sentence in 0.135 s, not the 0.5-20 s originally budgeted.

**What does not work, so it is not retried:** nothing re-runs the filter on its
own; `RimeSetInput` with an unchanged string is a no-op; modifier, arrow and
page keys never re-run the filter; `org.fcitx.Fcitx.Rime1.SetAsciiMode` does
call `RimeSetOption` and would re-run the filter, but never calls `updateUI`, so
fcitx5 never rebuilds the list; librime's notification handler is deploy and
schema messages only, and fcitx5-rime's async path deliberately refreshes the
status area rather than the candidates.

### Constraints any design has to honour

**The binding must reach every deployed schema.** Verified the hard way: with no
binding installed, an injected F24 reached the terminal as the escape sequence
`^[[24;2~`. A user who switches to a schema the patch missed would have stray
escape sequences typed into their applications. The per-schema
`<id>.custom.yaml` mechanism already covers this; it has to be used.

**The binding must be `when: always`.** With `when: composing` the key is not
consumed while idle and leaks to the application.

**The cache key must be the composed text, not `ctx.input`.** Measured: after
selecting a candidate, the filter re-runs with `input` unchanged but a different
sentence, so keying on the pinyin returns another sentence's translation.

**Inject only while a request is genuinely outstanding**, so no synthetic key is
ever produced in any other circumstance.

**Runtime dependency on `wtype`, and Wayland only.** A persistent virtual
keyboard would avoid re-attaching per answer if the churn ever proves to matter.

### Still open

- Settings: endpoint, model, target language, hotkey, and the off switch.
  Default endpoint is local (`http://127.0.0.1:1234/v1`); a user who points it
  at a remote host has chosen that, and no warning is owed to them.
- What the candidate looks like while the request is in flight, and whether a
  failure shows anything at all or simply yields no candidate.
- Cancellation: what happens to the child and its answer when the user commits
  or clears the composition first.
- Where the answer is handed back, and the read/write race with the child.

---

## Integration this plugin does not own yet

Three pieces of a working setup live outside the plugin. On the machine this was
developed on they are already in place by hand, so the gap only shows on a fresh
install elsewhere.

- The Hyprland left-Shift tap that toggles the input method
  (`~/.config/hypr/input.lua`)
- The layer rule that keeps candidate windows unblurred
  (`~/.config/hypr/looknfeel.lua`)
- The `QT_IM_MODULE` override in `~/.config/environment.d/`

See the README for which of these the plugin now manages and which it only
reports on.
