(() => {
  const setLoginStatus = (text, state = "info") => {
    const loginStatus = document.querySelector("[data-login-status]");
    if (!loginStatus) return;
    loginStatus.innerHTML = "";
    if (!text) return;
    const message = document.createElement("p");
    message.className = `login-state login-state-${state}`;
    message.setAttribute("role", state === "error" ? "alert" : "status");
    message.textContent = text;
    loginStatus.appendChild(message);
  };
  document.addEventListener("submit", (event) => {
    const authForm = event.target.closest?.("[data-auth-form]");
    if (!authForm) return;
    authForm.classList.add("is-loading");
    authForm.querySelector("button[type='submit']")?.setAttribute("aria-busy", "true");
  });
  if (document.querySelector("[data-login-form]")) {
    document.addEventListener("submit", (event) => {
      const loginForm = event.target.closest?.("[data-login-form]");
      if (!loginForm) return;
      loginForm.classList.add("is-loading");
      loginForm.querySelector("button[type='submit']")?.setAttribute("aria-busy", "true");
      setLoginStatus("Connexion sécurisée en cours…");
    });
    document.body.addEventListener("htmx:beforeSwap", (event) => {
      if (!event.detail.requestConfig?.elt?.matches?.("[data-login-form]")) return;
      const destination = event.detail.xhr?.responseURL || "";
      if (destination && !destination.includes("/auth/login/")) {
        setLoginStatus("Identifiants validés. Redirection sécurisée…", "success");
      }
    });
    for (const eventName of ["htmx:responseError", "htmx:sendError", "htmx:timeout"]) {
      document.body.addEventListener(eventName, (event) => {
        if (!event.detail.requestConfig?.elt?.matches?.("[data-login-form]")) return;
        const loginForm = document.querySelector("[data-login-form]");
        loginForm?.classList.remove("is-loading");
        loginForm?.querySelector("button[type='submit']")?.removeAttribute("aria-busy");
        setLoginStatus("La connexion n’a pas pu aboutir. Vérifiez votre réseau et réessayez.", "error");
      });
    }
  }

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest?.("[data-password-toggle]");
    if (!toggle) return;
    const inputId = toggle.getAttribute("aria-controls");
    const input = inputId ? document.getElementById(inputId) : null;
    if (!input) return;
    const willReveal = input.type === "password";
    input.type = willReveal ? "text" : "password";
    toggle.setAttribute("aria-pressed", String(willReveal));
    toggle.setAttribute("aria-label", willReveal ? "Masquer le mot de passe" : "Afficher le mot de passe");
    const openEye = toggle.querySelector(".password-eye-open");
    const closedEye = toggle.querySelector(".password-eye-closed");
    if (openEye) openEye.hidden = willReveal;
    if (closedEye) closedEye.hidden = !willReveal;
  });

  const workspace = document.querySelector("[data-workspace]");
  if (!workspace) return;

  const sidebar = workspace.querySelector("[data-sidebar]");
  const openButton = workspace.querySelector("[data-nav-open]");
  const closeButton = workspace.querySelector("[data-nav-close]");
  const backdrop = workspace.querySelector("[data-nav-backdrop]");
  const profileButton = workspace.querySelector("[data-profile-toggle]");
  const profileDropdown = workspace.querySelector("[data-profile-dropdown]");

  const isMobile = () => window.matchMedia("(max-width: 820px)").matches;
  const setNavigation = (open, restoreFocus = false) => {
    sidebar?.classList.toggle("is-open", open);
    if (backdrop) backdrop.hidden = !open;
    openButton?.setAttribute("aria-expanded", String(open));
    sidebar?.setAttribute("aria-hidden", String(isMobile() && !open));
    document.body.classList.toggle("nav-open", open && isMobile());
    if (open) sidebar?.querySelector("a")?.focus();
    if (!open && restoreFocus) openButton?.focus();
  };

  const setProfile = (open, restoreFocus = false) => {
    if (profileDropdown) profileDropdown.hidden = !open;
    profileButton?.setAttribute("aria-expanded", String(open));
    if (open) profileDropdown?.querySelector("button, a")?.focus();
    if (!open && restoreFocus) profileButton?.focus();
  };

  openButton?.addEventListener("click", () => setNavigation(true));
  closeButton?.addEventListener("click", () => setNavigation(false, true));
  backdrop?.addEventListener("click", () => setNavigation(false, true));
  profileButton?.addEventListener("click", () => setProfile(profileDropdown?.hidden ?? true));
  workspace.addEventListener("click", (event) => {
    if (profileDropdown && !profileDropdown.hidden && !event.target.closest(".profile-menu")) setProfile(false);
    if (isMobile() && event.target.closest(".workspace-link")) setNavigation(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (sidebar?.classList.contains("is-open")) setNavigation(false, true);
    else if (profileDropdown && !profileDropdown.hidden) setProfile(false, true);
  });
  window.addEventListener("resize", () => setNavigation(false));
  setNavigation(false);

  document.addEventListener("submit", (event) => {
    const button = event.target.querySelector("button[type='submit']");
    if (!button || button.disabled) return;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  });
})();
