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

function renderEra(state) {
  const era = state.era;
  if (!era) return;
  $('eraPanelTitle').textContent = era.name;
  $('eraCurrency').textContent = `Валюта эпохи: ${era.currency}`;
  $('eraNumber').textContent = `ЭПОХА ${era.current} / 4`;
  $('eraName').textContent = era.currency;
  $('currencyLabel').textContent = era.currency.toUpperCase();
  const progress = era.progress || {};
  const names = {
    taps: 'Нажатия только в первой эпохе',
    upgrade_purchases: 'Покупки улучшений только во второй эпохе',
    coins_earned_era2_and_era3: 'Монеты, заработанные во второй и третьей эпохах'
  };
  if (progress.next_era) {
    $('eraRequirement').textContent = `Для эпохи ${progress.next_era}: ${names[progress.metric] || 'условие перехода'}`;
    $('eraProgressText').textContent = `${fmt.format(progress.progress)} / ${fmt.format(progress.target)}`;
    $('eraProgressFill').style.width = `${Math.min(100, progress.target ? progress.progress / progress.target * 100 : 0)}%`;
    $('eraTransitionBtn').hidden = false;
    $('eraTransitionBtn').disabled = !progress.unlocked;
    $('eraTransitionBtn').textContent = progress.unlocked
      ? `Открыть эпоху ${progress.next_era} · переход необратим`
      : `Осталось: ${fmt.format(progress.remaining)}`;
  } else {
    $('eraRequirement').textContent = 'Ты достиг последней эпохи — Божественного измерения.';
    $('eraProgressText').textContent = 'МАКСИМАЛЬНАЯ ЭПОХА';
    $('eraProgressFill').style.width = '100%';
    $('eraTransitionBtn').hidden = true;
  }
  const archive = $('eraArchive');
  archive.replaceChildren();
  (era.archive || []).forEach((item) => {
    const entry = document.createElement('span');
    entry.className = 'era-archive-item';
    entry.textContent = `${item.name}: баланс ${fmt.format(item.balance)} ${item.currency} · заработано ${fmt.format(item.earned)} · ${fmt.format(item.taps)} тапов · улучшений ${fmt.format(item.upgrade_purchases)}`;
    archive.append(entry);
  });
}

function renderProfile(state) {
  const profile = state.profile;
  if (!profile) return;
  $('profileAvatar').value = profile.avatar;
  $('profileFrame').value = profile.frame;
  $('publicProfileLink').value = `${window.location.origin}${profile.url}`;
}

async function saveProfile() {
  try {
    const data = await api('/api/profile/update', 'POST', {
      avatar: $('profileAvatar').value,
      frame: $('profileFrame').value,
    });
    if (data.state) renderState(data.state);
    if (data.ok) showToast('Оформление профиля сохранено!');
    else showToast(data.error || 'Не удалось сохранить профиль.');
  } catch (error) { showToast(error.message); }
}

async function openPublicProfile(code) {
  try {
    const data = await api(`/api/profile/${encodeURIComponent(code)}`);
    if (!data.ok) throw new Error(data.error || 'Профиль не найден.');
    const profile = data.profile;
    $('profileDialogAvatar').textContent = profile.avatar;
    $('profileDialogTitle').textContent = profile.nickname;
    $('profileDialogEra').textContent = `Эпоха ${profile.era} · ${profile.frame} рамка`;
    const stats = $('profileDialogStats');
    stats.replaceChildren();
    [
      [`Заработано ${fmt.format(profile.total_earned)}`, 'монет'],
      [`Тапы ${fmt.format(profile.total_taps)}`, 'нажатий'],
      [`Достижения ${profile.achievements.length}`, 'открыто'],
    ].forEach(([value, label]) => {
      const item = document.createElement('div');
      const strong = document.createElement('b');
      strong.textContent = value;
      const small = document.createElement('small');
      small.textContent = label;
      item.append(strong, small);
      stats.append(item);
    });
    const collection = $('profileDialogCollection');
    collection.replaceChildren();
    const heading = document.createElement('h3');
    heading.textContent = 'Коллекция и достижения';
    collection.append(heading);
    [...profile.achievements.map(item => `${item.badge} ${item.title}`),
      ...profile.cosmetics.map(code => `🎨 ${code}`),
      ...profile.gifts.map(item => `${item.icon} ${item.title}`)].forEach((value) => {
      const line = document.createElement('p');
      line.textContent = value;
      collection.append(line);
    });
    if (collection.children.length === 1) {
      const empty = document.createElement('p');
      empty.textContent = 'Пока коллекция пуста.';
      collection.append(empty);
    }
    $('profileDialog').showModal();
  } catch (error) { showToast(error.message); }
}

function renderReferrals(state) {
  const data = state.referrals;
  if (!data) return;
  $('referralLink').value = data.link;
  $('referralInvited').textContent = fmt.format(data.invited);
  $('referralActive').textContent = fmt.format(data.active);
  $('referralPending').textContent = fmt.format(data.pending);
  $('referralRewards').textContent = fmt.format(data.rewards);
}

function renderGifts(state) {
  const data = state.gifts;
  if (!data) return;
  $('giftDailyLimit').textContent = `${fmt.format(data.sent_today)} / ${fmt.format(data.daily_limit)} сегодня`;
  $('giftActiveCount').textContent = `Активно ${data.active_boosts} / 2 буста`;
  const select = $('giftSelect');
  if (select.options.length === 0) {
    data.catalog.forEach((item) => {
      const option = document.createElement('option');
      option.value = item.code;
      option.textContent = `${item.icon} ${item.title} · ${item.rarity === 'common' ? 'обычный' : item.rarity === 'rare' ? 'редкий' : item.rarity === 'epic' ? 'эпический' : 'легендарный'}`;
      select.append(option);
    });
  }
  $('giftSendBtn').disabled = data.remaining_today <= 0;
  const root = $('giftInventory');
  root.replaceChildren();
  if (!data.inventory.length) {
    const empty = document.createElement('div');
    empty.className = 'loading-card panel';
    empty.textContent = 'Пока подарков нет. Попроси друга отправить тебе что-нибудь праздничное!';
    root.append(empty);
    return;
  }
  data.inventory.forEach((item) => {
    const card = document.createElement('article');
    card.className = `gift-card panel rarity-${item.rarity}`;
    const icon = document.createElement('div');
    icon.className = 'gift-card-icon';
    icon.textContent = item.icon;
    const body = document.createElement('div');
    body.className = 'gift-card-body';
    const title = document.createElement('h3');
    title.textContent = item.title;
    const description = document.createElement('p');
    description.textContent = item.description;
    const sender = document.createElement('small');
    sender.textContent = `От игрока: ${item.sender}${item.note ? ' · ' + item.note : ''}`;
    const status = document.createElement('small');
    const statusNames = { inventory: 'В инвентаре', active: `Активен · ${Math.ceil(item.remaining_seconds / 60)} мин.`, expired: 'Время действия закончилось', collected: 'В коллекции' };
    status.textContent = statusNames[item.status] || item.status;
    body.append(title, description, sender, status);
    const action = document.createElement('div');
    if (item.can_activate) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'secondary-button';
      button.textContent = 'Активировать';
      button.disabled = data.active_boosts >= 2;
      button.addEventListener('click', () => activateGift(item.id));
      action.append(button);
    }
    card.append(icon, body, action);
    root.append(card);
  });
}

async function sendGift(event) {
  event.preventDefault();
  const recipient_code = $('giftRecipientCode').value.trim();
  const gift_code = $('giftSelect').value;
  const note = $('giftNote').value.trim();
  $('giftSendBtn').disabled = true;
  try {
    const data = await api('/api/gifts/send', 'POST', { recipient_code, gift_code, note });
    if (data.state) renderState(data.state);
    if (data.ok) {
      $('giftRecipientCode').value = '';
      $('giftNote').value = '';
      showToast(data.message || 'Подарок отправлен!');
    } else showToast(data.error || 'Не удалось отправить подарок.');
  } catch (error) { showToast(error.message); }
  finally { $('giftSendBtn').disabled = false; }
}

async function activateGift(gift_id) {
  try {
    const data = await api('/api/gifts/activate', 'POST', { gift_id });
    if (data.state) renderState(data.state);
    if (data.ok) showToast(data.message || 'Подарочный буст активирован!');
    else showToast(data.error || 'Не удалось активировать подарок.');
  } catch (error) { showToast(error.message); }
}

function renderWinterEvent(state) {
  const event = state.winter_event;
  if (!event) return;
  $('winterTicketCount').textContent = `🎟 ${fmt.format(event.tickets)} билетиков`;
  const calendar = event.calendar || {};
  const claimed = new Set(event.claimed_days || []);
  const day = Number(calendar.day || 0);
  if (calendar.finished) {
    $('winterDayTitle').textContent = 'Календарь завершён';
    $('winterDayDescription').textContent = 'Все 31 день события позади. Накопленные билетики остаются у тебя.';
    $('winterClaimBtn').hidden = true;
  } else if (!calendar.active) {
    $('winterDayTitle').textContent = 'Скоро открытие';
    $('winterDayDescription').textContent = 'Праздничный календарь ещё не начался.';
    $('winterClaimBtn').hidden = true;
  } else {
    $('winterDayTitle').textContent = `День ${day} из 31`;
    $('winterDayDescription').textContent = calendar.claimed_today
      ? 'Сегодняшний подарок уже получен.'
      : `Сегодня можно забрать ${fmt.format(calendar.today_reward)} билетиков. Пропущенный день сгорает.`;
    $('winterClaimBtn').hidden = false;
    $('winterClaimBtn').disabled = Boolean(calendar.claimed_today);
    $('winterClaimBtn').textContent = calendar.claimed_today
      ? 'Получено ✓'
      : `Забрать +${fmt.format(calendar.today_reward)} 🎟`;
  }
  const root = $('winterDays');
  root.replaceChildren();
  for (let number = 1; number <= 31; number += 1) {
    const tile = document.createElement('div');
    const isClaimed = claimed.has(number);
    const isCurrent = number === day && calendar.active && !isClaimed;
    const isExpired = number < day && !isClaimed;
    tile.className = `winter-day${isClaimed ? ' is-claimed' : isCurrent ? ' is-current' : isExpired ? ' is-expired' : ''}`;
    const numberLabel = document.createElement('b');
    numberLabel.textContent = String(number);
    const rewardLabel = document.createElement('span');
    rewardLabel.textContent = `+${fmt.format(10 + (number - 1) * 2)}`;
    const status = document.createElement('small');
    status.textContent = isClaimed ? 'Получено' : isExpired ? 'Пропущено' : isCurrent ? 'Сегодня' : 'Закрыто';
    tile.append(numberLabel, rewardLabel, status);
    root.append(tile);
  }
  const shop = $('winterShop');
  shop.replaceChildren();
  (event.shop || []).forEach((item) => {
    const card = document.createElement('article');
    card.className = `winter-shop-item panel${item.owned ? ' is-owned' : ''}`;
    const icon = document.createElement('div');
    icon.className = 'winter-shop-icon';
    icon.textContent = item.icon;
    const title = document.createElement('h3');
    title.textContent = item.title;
    const description = document.createElement('p');
    description.textContent = item.description;
    const era = document.createElement('small');
    era.textContent = `Открывается с эпохи ${item.era_required}`;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = item.owned ? 'secondary-button' : 'primary-button';
    button.textContent = item.owned ? 'В коллекции ✓' : `Купить · ${fmt.format(item.price)} 🎟`;
    button.disabled = item.owned || !item.available || event.tickets < item.price;
    if (!item.owned && item.available) button.addEventListener('click', () => purchaseWinterItem(item.code));
    card.append(icon, title, description, era, button);
    shop.append(card);
  });
}

async function purchaseWinterItem(item_code) {
  try {
    const data = await api('/api/winter/shop/purchase', 'POST', { item_code });
    if (data.state) renderState(data.state);
    if (data.ok) showToast(data.message || 'Предмет добавлен в коллекцию!');
    else showToast(data.error || 'Не удалось купить предмет.');
  } catch (error) { showToast(error.message); }
}

function renderState(state) {
  currentState = state;
  renderEra(state);
  renderWinterEvent(state);
  renderGifts(state);
  renderReferrals(state);
  renderProfile(state);
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
  renderAchievements(state);
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

function renderAchievements(state) {
  const root = $('achievementsList');
  if (!root) return;
  root.replaceChildren();
  (state.achievements || []).forEach((item) => {
    const card = document.createElement('article');
    card.className = `reward-card panel${item.claimed ? ' is-claimed' : item.unlocked ? ' is-unlocked' : ''}`;
    const badge = document.createElement('div');
    badge.className = 'reward-badge';
    badge.textContent = item.badge || '★';
    const body = document.createElement('div');
    body.className = 'reward-body';
    const title = document.createElement('h3');
    title.textContent = item.title;
    const description = document.createElement('p');
    description.textContent = item.description;
    const meta = document.createElement('p');
    meta.textContent = `${fmt.format(item.progress)} / ${fmt.format(item.target)} · награда ${fmt.format(item.reward)} ${currentState?.era?.currency || 'монет'}`;
    const track = document.createElement('div');
    track.className = 'reward-track compact';
    const fill = document.createElement('div');
    fill.style.width = `${item.percent}%`;
    track.append(fill);
    body.append(title, description, meta, track);
    const action = document.createElement('div');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'reward-claim';
    button.textContent = item.claimed ? 'Получено' : item.unlocked ? 'Забрать' : `${item.percent}%`;
    button.disabled = item.claimed || !item.unlocked;
    if (item.unlocked && !item.claimed) button.addEventListener('click', () => claimAchievement(item.id));
    action.append(button);
    card.append(badge, body, action);
    root.append(card);
  });
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
    const sort = $('leaderboardSort')?.value || 'earned';
    const data = await api(`/api/leaderboard?sort=${encodeURIComponent(sort)}`);
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
      name.textContent = `${player.avatar || '☁️'} ${player.nickname}`;
      if (player.you) {
        const you = document.createElement('small');
        you.textContent = 'это ты';
        name.append(you);
      }
      const score = document.createElement('strong');
      score.className = 'leader-score';
      score.textContent = `${fmt.format(player.score)} ${player.score_label}`;
      const profileButton = document.createElement('button');
      profileButton.type = 'button';
      profileButton.className = 'leader-profile-button';
      profileButton.textContent = 'Профиль';
      profileButton.addEventListener('click', () => openPublicProfile(player.profile_code));
      row.append(rank, name, score, profileButton);
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

async function claimWinterCalendar() {
  $('winterClaimBtn').disabled = true;
  try {
    const data = await api('/api/winter/calendar/claim', 'POST', {});
    if (data.state) renderState(data.state);
    if (data.ok) showToast(`Новогодний подарок: +${fmt.format(data.reward)} билетиков!`);
    else showToast(data.error || 'Не удалось получить подарок.');
  } catch (error) {
    showToast(error.message);
  }
}

async function transitionEra() {
  if (!currentState?.era?.progress?.unlocked) return;
  const confirmed = window.confirm('Переход необратим. Баланс и улучшения текущей эпохи сохранятся в архиве, а новая эпоха начнётся с нуля. Перейти?');
  if (!confirmed) return;
  $('eraTransitionBtn').disabled = true;
  try {
    const data = await api('/api/era/transition', 'POST', {});
    if (data.state) renderState(data.state);
    if (data.ok) {
      showToast(`Открыта эпоха: ${data.state.era.name}!`);
      refreshLeaderboard();
    } else {
      showToast(data.error || 'Не удалось перейти в новую эпоху.');
    }
  } catch (error) {
    showToast(error.message);
  } finally {
    if (currentState?.era?.progress?.unlocked) $('eraTransitionBtn').disabled = false;
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

async function claimAchievement(id) {
  try {
    const data = await api('/api/achievement/claim', 'POST', { id });
    if (data.state) renderState(data.state);
    if (data.ok) showToast(`Достижение получено: +${fmt.format(data.reward)} ${data.state.era.currency}!`);
    else showToast(data.error || 'Награда пока недоступна.');
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
$('eraTransitionBtn').addEventListener('click', transitionEra);
$('winterClaimBtn').addEventListener('click', claimWinterCalendar);
$('copyReferralBtn').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('referralLink').value);
    showToast('Реферальная ссылка скопирована!');
  } catch {
    $('referralLink').select();
    showToast('Выделил ссылку — скопируй её вручную.');
  }
});
$('giftSendForm').addEventListener('submit', sendGift);
$('saveProfileBtn').addEventListener('click', saveProfile);
$('copyProfileBtn').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('publicProfileLink').value);
    showToast('Ссылка на профиль скопирована!');
  } catch {
    $('publicProfileLink').select();
    showToast('Выделил ссылку — скопируй её вручную.');
  }
});

$('leaderboardSort').addEventListener('change', refreshLeaderboard);
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
