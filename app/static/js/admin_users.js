document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".js-delete-user-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm("Are you sure you want to delete this user?")) {
        event.preventDefault();
      }
    });
  });
});
