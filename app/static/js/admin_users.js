document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".js-remove-profile-image-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm("Remove this user's profile image?")) {
        event.preventDefault();
      }
    });
  });

  document.querySelectorAll(".js-delete-user-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm("Are you sure you want to delete this user?")) {
        event.preventDefault();
      }
    });
  });
});
