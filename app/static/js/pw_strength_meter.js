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

    // Length-first feedback only. Server-side policy remains authoritative.
    if (password.length >= 8) score++;
    if (password.length >= 12) score++;
    if (password.length >= 20) score++;
    if (password.length >= 32) score++;
    return Math.min(score, 4);
  }
});
