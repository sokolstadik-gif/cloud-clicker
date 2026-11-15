const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const tgApp = window.Telegram?.WebApp || null;
const telegramInitData = tgApp?.initData || '';
if (tgApp) {
  document.body.classList.add('telegram-mini-app');
  tgApp.ready();
  tgApp.expand();
  try { tgApp.setHeaderColor('#080b10'); tgApp.setBackgroundColor('#080b10'); } catch (_) {}
}
const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const $ = (id) => document.getElementById(id);
let currentState = null;
let pendingClicks = 0;
let clickRequestBusy = false;
let toastTimer = null;

async function api(url, method = 'GET', body = undefined) {
  const options = { method, headers: { 'Accept': 'application/json' } };
  if (telegramInitData) options.headers['X-Telegram-Init-Data'] = telegramInitData;
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.headers['X-CSRF-Token'] = csrfToken;
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); }
  catch { throw new Error('Сервер вернул непонятный ответ.'); }
  if (!response.ok && !data.state) throw new Error(data.error || 'Ошибка запроса.');
  return data;
}

function showToast(message) {
  const toast = $('toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 2200);
}

function floatCoin(x, y, text = '+1 CC') {
  const node = document.createElement('span');
  node.className = 'float-coin';
  node.textContent = text;
  node.style.left = `${x}px`;
  node.style.top = `${y}px`;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 750);
}

function renderState(state) {
  currentState = state;
  $('coins').textContent = fmt.format(state.coins);
  $('tapPower').textContent = `+${fmt.format(state.tap_power)}`;
  $('passiveIncome').textContent = `+${fmt.format(state.passive_per_sec)}`;
  $('energyText').textContent = `${fmt.format(Math.floor(state.energy))} / ${fmt.format(state.max_energy)}`;
  $('energyFill').style.width = `${Math.max(0, Math.min(100, state.energy / state.max_energy * 100))}%`;
  $('energyNote').textContent = `Восстановление: ${state.energy_regen.toFixed(1).replace('.', ',')} ед./сек.`;
  $('levelPill').textContent = `Уровень ${1 + state.total_upgrade_levels}`;
  if (document.activeElement !== $('nicknameInput')) $('nicknameInput').value = state.nickname;
  $('dailyBtn').disabled = !state.daily_available;
  $('dailyBtn').textContent = state.daily_available ? `Забрать +${fmt.format(state.daily_reward)} CC` : 'Уже получено ✓';
  $('tapBtn').disabled = state.energy < 1;

  $('statsBalance').textContent = fmt.format(state.coins);
  $('statsEarned').textContent = fmt.format(state.total_earned);
  $('statsTaps').textContent = fmt.format(state.total_taps);
  $('statsTapPower').textContent = `+${fmt.format(state.tap_power)}`;
  $('statsPassive').textContent = `+${fmt.format(state.passive_per_sec)}`;
  $('statsUpgrades').textContent = fmt.format(state.total_upgrade_levels);
  $('statsEnergy').textContent = `${fmt.format(Math.floor(state.energy))} / ${fmt.format(state.max_energy)}`;
  $('statsRegen').textContent = `Восстановление ${state.energy_regen.toFixed(1).replace('.', ',')} ед./сек.`;
  $('statsLevel').textContent = `Уровень ${1 + state.total_upgrade_levels}`;
  $('rewardTapCount').textContent = `${fmt.format(state.total_taps)} тапов`;

  renderUpgrades(state);
  renderRewards(state);
  renderCombo(state);
  renderCipher(state);
  renderQuests(state);
  renderBoosts(state);
}

function renderUpgrades(state) {
  const root = $('upgradeList');
  root.replaceChildren();
  for (const item of state.upgrades) {
    const card = document.createElement('article');
    card.className = 'upgrade-card';

    const top = document.createElement('div');
    top.className = 'upgrade-top';
    const icon = document.createElement('span');
    icon.className = 'upgrade-icon';
    icon.textContent = item.icon;
    const level = document.createElement('span');
    level.className = 'upgrade-level';
    level.textContent = `Ур. ${item.level}`;
    top.append(icon, level);

    const title = document.createElement('h3');
    title.textContent = item.title;
    const category = document.createElement('span');
    category.className = 'upgrade-category';
    category.textContent = item.category || 'Улучшение';
    const description = document.createElement('p');
    description.textContent = item.description;
    const buy = document.createElement('button');
    buy.type = 'button';
    buy.className = 'buy-button';
    buy.disabled = state.coins < item.cost;
    buy.textContent = `Купить · ${fmt.format(item.cost)} CC`;
    buy.addEventListener('click', () => buyUpgrade(item.id));
    card.append(top, category, title, description, buy);
    root.append(card);
  }
}

function renderRewards(state) {
  const root = $('rewardsList');
  root.replaceChildren();
  const rewards = state.tap_rewards || [];
  const next = rewards.find((item) => !item.claimed);
  if (next) {
    const pct = Math.min(100, next.progress);
    const note = document.createElement('div');
    note.className = 'reward-progress panel';
    const label = document.createElement('div');
    label.className = 'reward-progress-label';
    const text = document.createElement('span');
    text.textContent = `Следующая награда: ${fmt.format(next.taps)} тапов`;
    const val = document.createElement('b');
    val.textContent = `${fmt.format(Math.min(state.total_taps, next.taps))} / ${fmt.format(next.taps)}`;
    label.append(text, val);
    const track = document.createElement('div');
    track.className = 'reward-track';
    const fill = document.createElement('div');
    fill.style.width = `${pct}%`;
    track.append(fill);
    note.append(label, track);
    root.append(note);
  }

  rewards.forEach((item) => {
    const card = document.createElement('article');
    card.className = `reward-card panel${item.claimed ? ' is-claimed' : item.unlocked ? ' is-unlocked' : ''}`;
    const badge = document.createElement('div');
    badge.className = 'reward-badge';
    badge.textContent = item.claimed ? '✓' : '☁';
    const body = document.createElement('div');
    body.className = 'reward-body';
    const title = document.createElement('h3');
    title.textContent = `${fmt.format(item.taps)} тапов`;
    const description = document.createElement('p');
    description.textContent = `Награда: ${fmt.format(item.reward)} Cloudcoin`;
    const progress = document.createElement('div');
    progress.className = 'reward-track compact';
    const progressFill = document.createElement('div');
    progressFill.style.width = `${Math.min(100, item.progress)}%`;
    progress.append(progressFill);
    body.append(title, description, progress);

    const action = document.createElement('div');
    action.className = 'reward-action';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'reward-claim';
    if (item.claimed) {
      button.textContent = 'Получено';
      button.disabled = true;
    } else if (item.unlocked) {
      button.textContent = 'Забрать';
      button.addEventListener('click', () => claimTapReward(item.taps));
    } else {
      button.textContent = `${Math.min(100, item.progress)}%`;
      button.disabled = true;
    }
    action.append(button);
    card.append(badge, body, action);
    root.append(card);
  });
}

function renderCombo(state) {
  const combo = state.daily_combo;
  if (!combo) return;
  const root = $('comboCards');
  root.replaceChildren();
  combo.cards.forEach((item, index) => {
    const card = document.createElement('article');
    card.className = `combo-card panel${item.done ? ' is-done' : ''}`;
    const number = document.createElement('span');
    number.className = 'combo-number';
    number.textContent = String(index + 1).padStart(2, '0');
    const icon = document.createElement('span');
    icon.className = 'combo-icon';
    icon.textContent = item.done ? '✓' : item.icon;
    const title = document.createElement('b');
    title.textContent = item.title;
    const status = document.createElement('small');
    status.textContent = item.done ? 'Улучшено сегодня' : 'Нужно купить уровень';
    card.append(number, icon, title, status);
    root.append(card);
  });
  const doneCount = combo.cards.filter((item) => item.done).length;
  $('comboStatus').textContent = combo.claimed ? 'Награда уже получена сегодня' : `Выполнено ${doneCount} из 3`;
  $('comboClaimBtn').disabled = !combo.completed || combo.claimed;
  $('comboClaimBtn').textContent = combo.claimed ? 'Получено ✓' : `Забрать ${fmt.format(combo.reward)} CC`;
}

function renderCipher(state) {
  const cipher = state.daily_cipher;
  if (!cipher) return;
  $('cipherMorse').textContent = cipher.morse;
  $('cipherSubmitBtn').disabled = cipher.claimed;
  $('cipherAnswer').disabled = cipher.claimed;
  $('cipherStatus').textContent = cipher.claimed
    ? 'Секретный код уже активирован сегодня ✓'
    : `Награда за правильный ответ — ${fmt.format(cipher.reward)} Cloudcoin.`;
  $('cipherSubmitBtn').textContent = cipher.claimed ? 'Получено ✓' : 'Проверить';
}

function renderQuests(state) {
  const root = $('dailyQuestList');
  root.replaceChildren();
  (state.daily_quests || []).forEach((quest) => {
    const card = document.createElement('article');
    card.className = `quest-card panel${quest.claimed ? ' is-claimed' : quest.unlocked ? ' is-ready' : ''}`;
    const icon = document.createElement('div');
    icon.className = 'quest-icon';
    icon.textContent = quest.claimed ? '✓' : quest.icon;
    const body = document.createElement('div');
    body.className = 'quest-main';
    const title = document.createElement('h3');
    title.textContent = quest.title;
    const description = document.createElement('p');
    description.textContent = quest.description;
    const meta = document.createElement('div');
    meta.className = 'quest-meta';
    const progressText = document.createElement('span');
    progressText.textContent = `${fmt.format(quest.progress)} / ${fmt.format(quest.target)}`;
    const rewardText = document.createElement('b');
    rewardText.textContent = `+${fmt.format(quest.reward)} CC`;
    meta.append(progressText, rewardText);
    const track = document.createElement('div');
    track.className = 'reward-track compact';
    const fill = document.createElement('div');
    fill.style.width = `${quest.percent}%`;
    track.append(fill);
    body.append(title, description, meta, track);
    const action = document.createElement('div');
    action.className = 'quest-action';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'reward-claim';
    button.textContent = quest.claimed ? 'Получено' : quest.unlocked ? 'Забрать' : 'В процессе';
    button.disabled = quest.claimed || !quest.unlocked;
    if (quest.unlocked && !quest.claimed) button.addEventListener('click', () => claimQuest(quest.id));
    action.append(button);
    card.append(icon, body, action);
    root.append(card);
  });
}

function renderBoosts(state) {
  const boosts = state.boosts;
  if (!boosts) return;
  $('turboStatus').textContent = boosts.turbo_active
    ? `x2 активно · ${boosts.turbo_remaining} сек.`
    : 'Удваивает награду за тап на 30 секунд';
  $('turboBtn').textContent = boosts.turbo_active ? 'Продлить · 500 CC' : 'Активировать · 500 CC';
  $('turboBtn').disabled = state.coins < boosts.turbo_cost;
  $('energyBoostStatus').textContent = boosts.energy_full ? 'Энергия уже полная' : 'Мгновенно заполнить шкалу';
  $('energyBoostBtn').disabled = boosts.energy_full || state.coins < boosts.energy_cost;
}

async function refreshShop() {
  const root = $('shopPackages');
  if (!root) return;
  try {
    const data = await api('/api/shop/packages');
    root.replaceChildren();
    data.packages.forEach((item) => {
      const card = document.createElement('article');
      card.className = 'shop-card panel';
      const icon = document.createElement('div');
      icon.className = 'shop-icon';
      icon.textContent = item.id === 'starter' ? '☁' : item.id === 'pro' ? '⚡' : '👑';
      const title = document.createElement('h2');
      title.textContent = item.title;
      const amount = document.createElement('strong');
      amount.className = 'shop-coins';
      amount.textContent = `${fmt.format(item.coins)} Cloudcoin`;
      const price = document.createElement('div');
      price.className = 'shop-price';
      price.textContent = `⭐ ${fmt.format(item.stars)} Stars`;
      const buy = document.createElement('button');
      buy.type = 'button';
      buy.className = 'primary-button';
      buy.textContent = data.payments_enabled ? 'Купить' : 'Магазин не настроен';
      buy.disabled = !data.payments_enabled || !telegramInitData;
      buy.addEventListener('click', () => buyStarPackage(item.id));
      card.append(icon, title, amount, price, buy);
      root.append(card);
    });
    if (!telegramInitData) {
      const note = document.createElement('p');
      note.className = 'privacy-note';
      note.textContent = 'Открой Cloud Clicker из Telegram, чтобы войти в аккаунт и пользоваться покупками.';
      root.append(note);
    }
  } catch (error) {
    root.textContent = error.message;
  }
}

async function buyStarPackage(packageId) {
  try {
    const data = await api('/api/shop/create-invoice', 'POST', { package_id: packageId });
    if (!data.ok || !data.invoice_url) throw new Error(data.error || 'Не удалось создать счёт.');
    if (tgApp?.openInvoice) {
      tgApp.openInvoice(data.invoice_url, (status) => {
        if (status === 'paid') {
          showToast('Оплата принята. Начисляем Cloudcoin…');
          setTimeout(refreshState, 1200);
          setTimeout(refreshState, 3500);
        } else if (status === 'failed') {
          showToast('Telegram не смог завершить оплату.');
        } else if (status === 'cancelled') {
          showToast('Оплата отменена.');
        }
      });
    } else {
      window.location.href = data.invoice_url;
    }
  } catch (error) {
    showToast(error.message);
  }
}

async function refreshState() {
  try {
    const data = await api('/api/state');
    if (data.ok) renderState(data.state);
  } catch (error) {
    showToast(error.message);
  }
}

async function refreshLeaderboard() {
  try {
    const data = await api('/api/leaderboard');
    $('myRank').textContent = data.my_rank ? `#${fmt.format(data.my_rank)}` : '—';
    $('statsRank').textContent = data.my_rank ? `#${fmt.format(data.my_rank)}` : '—';
    const root = $('leaderboard');
    root.replaceChildren();
    if (!data.leaders.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.textContent = 'Пока никто не попал в рейтинг. Начни первым!';
      root.append(empty);
      return;
    }
    data.leaders.forEach((player) => {
      const row = document.createElement('div');
      row.className = `leader-row${player.you ? ' is-you' : ''}`;
      const rank = document.createElement('span');
      rank.className = 'leader-rank';
      rank.textContent = String(player.rank).padStart(2, '0');
      const name = document.createElement('span');
      name.className = 'leader-name';
      name.textContent = player.nickname;
      if (player.you) {
        const you = document.createElement('small');
        you.textContent = 'это ты';
        name.append(you);
      }
      const score = document.createElement('strong');
      score.className = 'leader-score';
      score.textContent = `${fmt.format(player.total_earned)} CC`;
      row.append(rank, name, score);
      root.append(row);
    });
  } catch (error) {
    $('leaderboard').textContent = 'Не удалось загрузить рейтинг.';
  }
}

async function drainClicks() {
  if (clickRequestBusy || pendingClicks < 1) return;
  clickRequestBusy = true;
  const count = Math.min(10, pendingClicks);
  pendingClicks -= count;
  try {
    const data = await api('/api/click', 'POST', { count });
    if (data.state) renderState(data.state);
    if (data.ok) {
      if (data.accepted_count < count) pendingClicks = 0;
      if (data.earned >= 5) showToast(`+${fmt.format(data.earned)} Cloudcoin`);
    } else {
      pendingClicks = 0;
      showToast(data.error || 'Не получилось начислить Cloudcoin.');
      if (data.state) renderState(data.state);
    }
  } catch (error) {
    pendingClicks = 0;
    showToast(error.message);
  } finally {
    clickRequestBusy = false;
    if (pendingClicks > 0) setTimeout(drainClicks, 40);
    refreshLeaderboard();
  }
}

async function buyUpgrade(id) {
  try {
    const data = await api('/api/upgrade', 'POST', { id });
    if (data.state) renderState(data.state);
    if (data.ok) {
      const item = data.state.upgrades.find((entry) => entry.id === id);
      showToast(`${item ? item.title : 'Улучшение'} куплено!`);
      refreshLeaderboard();
    } else showToast(data.error || 'Не удалось купить улучшение.');
  } catch (error) { showToast(error.message); }
}

async function claimDaily() {
  $('dailyBtn').disabled = true;
  try {
    const data = await api('/api/daily', 'POST', {});
    if (data.state) renderState(data.state);
    if (data.ok) {
      showToast(`Ежедневный бонус: +${fmt.format(data.state.daily_reward || 250)} Cloudcoin!`);
      refreshLeaderboard();
    } else {
      showToast(data.error || 'Бонус пока недоступен.');
    }
  } catch (error) {
    showToast(error.message);
    $('dailyBtn').disabled = false;
  }
}

async function claimTapReward(milestone) {
  try {
    const data = await api('/api/tap-reward/claim', 'POST', { milestone });
    if (data.state) renderState(data.state);
    if (data.ok) {
      showToast(`Награда получена: +${fmt.format(data.reward)} Cloudcoin!`);
      refreshLeaderboard();
    } else {
      showToast(data.error || 'Не удалось получить награду.');
    }
  } catch (error) { showToast(error.message); }
}


async function claimCombo() {
  try {
    const data = await api('/api/combo/claim', 'POST', {});
    if (data.state) renderState(data.state);
    if (data.ok) { showToast(`Комбо выполнено: +${fmt.format(data.reward)} CC!`); refreshLeaderboard(); }
    else showToast(data.error || 'Не удалось забрать комбо.');
  } catch (error) { showToast(error.message); }
}

async function claimCipher(event) {
  event.preventDefault();
  const answer = $('cipherAnswer').value.trim();
  if (!answer) { showToast('Введи ответ на шифр.'); return; }
  try {
    const data = await api('/api/cipher/claim', 'POST', { answer });
    if (data.state) renderState(data.state);
    if (data.ok) { $('cipherAnswer').value = ''; showToast(`Шифр разгадан: +${fmt.format(data.reward)} CC!`); refreshLeaderboard(); }
    else showToast(data.error || 'Код не принят.');
  } catch (error) { showToast(error.message); }
}

async function claimQuest(id) {
  try {
    const data = await api('/api/quest/claim', 'POST', { id });
    if (data.state) renderState(data.state);
    if (data.ok) { showToast(`Задание выполнено: +${fmt.format(data.reward)} CC!`); refreshLeaderboard(); }
    else showToast(data.error || 'Награда пока недоступна.');
  } catch (error) { showToast(error.message); }
}

async function activateBoost(url, successMessage) {
  try {
    const data = await api(url, 'POST', {});
    if (data.state) renderState(data.state);
    if (data.ok) { showToast(successMessage); refreshLeaderboard(); }
    else showToast(data.error || 'Бустер недоступен.');
  } catch (error) { showToast(error.message); }
}

async function saveNickname() {
  const nickname = $('nicknameInput').value.trim();
  try {
    const data = await api('/api/nickname', 'POST', { nickname });
    if (data.state) renderState(data.state);
    if (data.ok) {
      showToast('Имя сохранено');
      refreshLeaderboard();
    } else showToast(data.error || 'Не удалось сохранить имя.');
  } catch (error) { showToast(error.message); }
}

function activateTab(name) {
  document.querySelectorAll('.tab-button').forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('[data-panel]').forEach((panel) => {
    const active = panel.dataset.panel === name;
    panel.hidden = !active;
    panel.classList.toggle('active', active);
  });
  if (name === 'records') refreshLeaderboard();
  if (name === 'stats' || name === 'rewards') refreshState();
  if (name === 'shop') refreshShop();
}

document.querySelectorAll('.tab-button').forEach((button) => {
  button.addEventListener('click', () => activateTab(button.dataset.tab));
});
document.querySelectorAll('[data-open-tab="game"]').forEach((link) => {
  link.addEventListener('click', (event) => { event.preventDefault(); activateTab('game'); });
});

$('tapBtn').addEventListener('click', (event) => {
  if (!currentState || currentState.energy < 1) {
    showToast('Немного подожди — энергия восстанавливается.');
    return;
  }
  if (pendingClicks >= 40) return;
  pendingClicks += 1;
  const multiplier = currentState.boosts?.turbo_active ? 2 : 1;
  floatCoin(event.clientX || window.innerWidth / 2, event.clientY || window.innerHeight / 2, `+${currentState.tap_power * multiplier} CC`);
  drainClicks();
});
$('dailyBtn').addEventListener('click', claimDaily);
$('saveNameBtn').addEventListener('click', saveNickname);
$('nicknameInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') saveNickname();
});
$('comboClaimBtn').addEventListener('click', claimCombo);
$('cipherForm').addEventListener('submit', claimCipher);
$('turboBtn').addEventListener('click', () => activateBoost('/api/boost/turbo', 'Турбо-облако активировано: x2 нажатия!'));
$('energyBoostBtn').addEventListener('click', () => activateBoost('/api/boost/energy', 'Энергия полностью восстановлена!'));
$('fullscreenBtn').addEventListener('click', async () => {
  try {
    if (!document.fullscreenElement) {
      await document.documentElement.requestFullscreen();
      $('fullscreenBtn').textContent = '×';
      $('fullscreenBtn').title = 'Выйти из полноэкранного режима';
    } else {
      await document.exitFullscreen();
      $('fullscreenBtn').textContent = '⛶';
      $('fullscreenBtn').title = 'На весь экран';
    }
  } catch {
    showToast('Полноэкранный режим не поддерживается этим браузером.');
  }
});
document.addEventListener('fullscreenchange', () => {
  $('fullscreenBtn').textContent = document.fullscreenElement ? '×' : '⛶';
});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) { refreshState(); refreshLeaderboard(); }
});

const creatorDialog = $('creatorDialog');
$('creatorBtn').addEventListener('click', () => creatorDialog.showModal());
$('footerCreatorBtn').addEventListener('click', () => creatorDialog.showModal());
creatorDialog.addEventListener('click', (event) => {
  if (event.target === creatorDialog) creatorDialog.close();
});

(async function start() {
  if (!tgApp) showToast('Открой приложение через Telegram для синхронизации аккаунта.');
  await refreshState();
  await refreshLeaderboard();
  await refreshShop();
  setInterval(refreshState, 15000);
  setInterval(refreshLeaderboard, 45000);
})();
