export const THEME_STORAGE_KEY = "sre-workshop-theme";

export const THEMES = Object.freeze([
  { id: "vanilla", name: "Vanilla", description: "The familiar workshop" },
  { id: "prism", name: "Prism", description: "Luminous and precise" },
  { id: "fieldnotes", name: "Fieldnotes", description: "Warm and editorial" },
  { id: "nightshift", name: "Nightshift", description: "Dark and immersive" },
]);

function knownTheme(id) {
  return THEMES.some((theme) => theme.id === id);
}

export function restoreTheme() {
  let theme = "vanilla";
  let available = true;
  try {
    const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (saved !== null && !knownTheme(saved)) {
      console.warn("The saved workshop theme is unknown. Using Vanilla.");
    } else if (saved !== null) {
      theme = saved;
    }
  } catch (error) {
    available = false;
    console.warn("The workshop theme preference could not be read.", error);
  }
  document.documentElement.dataset.sreTheme = theme;
  document.documentElement.dataset.sreThemeStorage = available ? "available" : "unavailable";
  return theme;
}

export function selectTheme(theme) {
  if (!knownTheme(theme)) {
    throw new RangeError(`Unknown workshop theme: ${theme}`);
  }
  document.documentElement.dataset.sreTheme = theme;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    document.documentElement.dataset.sreThemeStorage = "available";
    return true;
  } catch (error) {
    document.documentElement.dataset.sreThemeStorage = "unavailable";
    console.warn("The workshop theme preference could not be saved.", error);
    return false;
  }
}

export class ThemePicker {
  constructor() {
    this.root = null;
    this.inputs = new Map();
    this.listening = false;
    if (!knownTheme(document.documentElement.dataset.sreTheme)) {
      restoreTheme();
    }
  }

  mount() {
    const header = document.querySelector(".md-header__inner");
    if (!header || this.root?.isConnected) {
      return false;
    }
    this.inputs.clear();
    this.root = element("div", "sre-themes");
    this.button = element("button", "sre-themes__button");
    this.button.type = "button";
    this.button.title = "Change workshop theme";
    this.button.setAttribute("aria-haspopup", "dialog");
    this.button.setAttribute("aria-expanded", "false");
    this.button.setAttribute("aria-controls", "sre-theme-panel");
    const swatch = element("span", "sre-themes__icon");
    swatch.setAttribute("aria-hidden", "true");
    for (let index = 0; index < 4; index += 1) {
      swatch.append(element("span", ""));
    }
    const kind = element("span", "sre-themes__kind", "Theme");
    this.name = element("span", "sre-themes__name");
    const arrow = element("span", "sre-themes__arrow");
    arrow.setAttribute("aria-hidden", "true");
    this.button.append(swatch, kind, this.name, arrow);

    this.panel = element("section", "sre-themes__panel");
    this.panel.id = "sre-theme-panel";
    this.panel.hidden = true;
    this.panel.setAttribute("role", "dialog");
    this.panel.setAttribute("aria-labelledby", "sre-theme-title");
    const heading = element("div", "sre-themes__heading");
    const title = element("p", "sre-themes__title", "Choose your atmosphere");
    title.id = "sre-theme-title";
    const close = element("button", "sre-themes__close", "Close");
    close.type = "button";
    close.setAttribute("aria-label", "Close theme picker");
    close.addEventListener("click", () => this.close(true));
    heading.append(title, close);
    const introduction = element(
      "p",
      "sre-themes__intro",
      "Same workshop. The look that feels like you.",
    );
    const choices = element("fieldset", "sre-themes__choices");
    choices.append(element("legend", "sre-themes__legend", "Workshop theme"));
    for (const theme of THEMES) {
      const choice = element("label", "sre-themes__choice");
      choice.dataset.theme = theme.id;
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "sre-workshop-theme";
      input.value = theme.id;
      input.className = "sre-themes__input";
      input.setAttribute("aria-label", `${theme.name}: ${theme.description}`);
      input.addEventListener("change", () => {
        if (input.checked) {
          const saved = selectTheme(theme.id);
          this.render(
            saved
              ? `${theme.name} selected. Your preference is saved in this browser.`
              : `${theme.name} applied. Browser storage is unavailable, so this choice may not survive a reload.`,
          );
        }
      });
      const preview = element("span", "sre-themes__preview");
      preview.setAttribute("aria-hidden", "true");
      preview.append(
        element("span", "sre-themes__preview-bar"),
        element("span", "sre-themes__preview-type", "Aa"),
        element("span", "sre-themes__preview-lines"),
        element("span", "sre-themes__check"),
      );
      const label = element("span", "sre-themes__label", theme.name);
      const description = element("span", "sre-themes__description", theme.description);
      choice.append(input, preview, label, description);
      choices.append(choice);
      this.inputs.set(theme.id, input);
    }
    this.status = element("p", "sre-themes__status");
    this.status.setAttribute("role", "status");
    this.panel.append(heading, introduction, choices, this.status);
    this.root.append(this.button, this.panel);
    const search = header.querySelector(".md-search");
    header.insertBefore(
      this.root,
      search ? search.nextSibling : header.querySelector(".sre-connection"),
    );
    this.button.addEventListener("click", () => {
      if (this.panel.hidden) {
        this.open();
      } else {
        this.close();
      }
    });
    this.button.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        this.open();
      }
    });
    this.root.addEventListener("focusout", (event) => {
      if (event.relatedTarget && !this.root.contains(event.relatedTarget)) {
        this.close();
      }
    });
    if (!this.listening) {
      document.addEventListener("click", (event) => {
        if (!this.root.contains(event.target)) {
          this.close();
        }
      });
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !this.panel.hidden) {
          event.preventDefault();
          this.close(true);
        }
      });
      window.addEventListener("storage", (event) => {
        if (event.key === THEME_STORAGE_KEY || event.key === null) {
          restoreTheme();
          this.render();
        }
      });
      this.listening = true;
    }
    this.render();
    return true;
  }

  render(message = "") {
    const current = document.documentElement.dataset.sreTheme;
    const theme = THEMES.find((item) => item.id === current);
    this.name.textContent = theme.name;
    this.button.setAttribute("aria-label", `Change workshop theme. Current theme: ${theme.name}`);
    this.root.dataset.theme = current;
    for (const [id, input] of this.inputs) {
      input.checked = id === current;
    }
    this.status.textContent =
      message ||
      (document.documentElement.dataset.sreThemeStorage === "unavailable"
        ? "Browser storage is unavailable. You can still change the theme for this page."
        : "Your preference stays in this browser. Content and screenshots are unchanged.");
  }

  open() {
    this.panel.hidden = false;
    this.button.setAttribute("aria-expanded", "true");
    this.inputs.get(document.documentElement.dataset.sreTheme).focus();
  }

  close(restoreFocus = false) {
    this.panel.hidden = true;
    this.button.setAttribute("aria-expanded", "false");
    if (restoreFocus) {
      this.button.focus();
    }
  }
}
import { element } from "./dom.js";
