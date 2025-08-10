// static/js/pw_strength_meter.js

document.addEventListener('DOMContentLoaded', function () {
  const passwordInput = document.getElementById('password');
  const meter = document.getElementById('strengthMeter');
  const strengthText = document.getElementById('strengthText');

  if (!passwordInput || !meter || !strengthText) return;

  passwordInput.addEventListener('input', function () {
    const val = passwordInput.value;
    const score = getPasswordStrength(val);
    meter.value = score;

    const feedback = [
      "Very weak",
      "Weak",
      "Okay",
      "Good",
      "Strong"
    ];

    strengthText.textContent = val.length === 0 ? "Enter a password" : `Strength: ${feedback[score]}`;
  });

  function getPasswordStrength(password) {
    let score = 0;
    if (!password) return score;

    // Basic scoring logic
    if (password.length >= 8) score++;
    if (/[A-Z]/.test(password)) score++;
    if (/[0-9]/.test(password)) score++;
    if (/[^A-Za-z0-9]/.test(password)) score++;
    return Math.min(score, 4);
  }
});
