// 共通ユーティリティ（Chart.js のデフォルト、数値整形、評価バッジ生成など）

const OutpatientCommon = (() => {
  const palette = {
    naika: '#5b8a72',
    geka: '#b25c2e',
    other: '#9a7c4e',
    primary: '#b25c2e',
    accent: '#3d6b7d',
    muted: '#6b6660',
  };

  function formatInt(n) {
    if (n == null || Number.isNaN(n)) return '-';
    return Math.round(n).toLocaleString('ja-JP');
  }

  function formatPct(n, digits = 1) {
    if (n == null || Number.isNaN(n)) return '-';
    return `${n.toFixed(digits)}%`;
  }

  function gradeFromPct(pct, inverse = false) {
    if (pct == null || Number.isNaN(pct)) return 'D';
    if (inverse) {
      if (pct <= 90) return 'S';
      if (pct <= 100) return 'A';
      if (pct <= 110) return 'B';
      if (pct <= 125) return 'C';
      return 'D';
    }
    if (pct >= 110) return 'S';
    if (pct >= 100) return 'A';
    if (pct >= 90) return 'B';
    if (pct >= 75) return 'C';
    return 'D';
  }

  function gradeBadge(grade) {
    const el = document.createElement('span');
    el.className = `grade grade-${grade}`;
    el.textContent = grade;
    return el.outerHTML;
  }

  function typeBadge(type) {
    const label = { naika: '内科', geka: '外科', other: 'その他' }[type] || type;
    return `<span class="type-badge type-${type}">${label}</span>`;
  }

  if (typeof window !== 'undefined' && window.Chart) {
    Chart.defaults.font.family =
      '-apple-system, BlinkMacSystemFont, "Hiragino Kaku Gothic ProN", "Yu Gothic", Meiryo, sans-serif';
    Chart.defaults.font.size = 12;
    Chart.defaults.color = '#2d2a26';
  }

  return { palette, formatInt, formatPct, gradeFromPct, gradeBadge, typeBadge };
})();
