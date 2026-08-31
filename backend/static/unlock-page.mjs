import {redeemBurnerLink} from './burner-links.mjs';

const title = document.getElementById('title');
const status = document.getElementById('status');
const result = document.getElementById('result');
const key = document.getElementById('key');
const copy = document.getElementById('copy');

try {
  const password = await redeemBurnerLink();
  key.textContent = password;
  result.classList.remove('hidden');
  title.textContent = 'Decryption key ready';
  status.textContent = 'Copy it now. Refreshing or reopening this link will not retrieve it again.';
  copy.addEventListener('click', async () => {
    await navigator.clipboard.writeText(password);
    copy.textContent = 'Copied';
  });
} catch (error) {
  title.textContent = 'Link unavailable';
  title.classList.add('gone');
  status.textContent = error instanceof Error ? error.message : 'This link was already opened or has expired.';
}
