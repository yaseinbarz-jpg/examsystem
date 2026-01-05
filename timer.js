// backend/static/js/timer.js
function startTimer(elementId, durationSeconds) {
  const display = document.getElementById(elementId);
  if (!display) return;
  let timer = parseInt(durationSeconds, 10);
  // persist key based on exam and student if provided (Optional)
  const interval = setInterval(() => {
    const minutes = Math.floor(timer / 60);
    const seconds = timer % 60;
    display.textContent = `${minutes}:${seconds < 10 ? '0'+seconds : seconds}`;
    if (--timer < 0) {
      clearInterval(interval);
      alert('زمان آزمون به پایان رسید');
      const form = document.getElementById('examForm');
      if (form) form.submit();
    }
  }, 1000);
}