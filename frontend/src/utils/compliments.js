/**
 * Random compliment shown during photo reveal.
 * Keeps the emotional peak warm and personal.
 */
const COMPLIMENTS = {
  en: [
    'You look stunning!',
    'Gorgeous!',
    'Picture perfect!',
    'Absolutely beautiful!',
    'What a moment!',
    'Love this one!',
    'So elegant!',
    'Beautiful memories!',
    'Magnifique!',
    'You look radiant!',
    'What a lovely group!',
    'Perfection!',
  ],
  fr: [
    'Tu es magnifique !',
    'Superbe !',
    'Une photo parfaite !',
    'Absolument magnifique !',
    'Quel moment !',
    'J\'adore celle-ci !',
    'Tellement élégant !',
    'De beaux souvenirs !',
    'Magnifique !',
    'Vous êtes rayonnants !',
    'Quel beau groupe !',
    'La perfection !',
  ]
};

let lastIndex = -1;

export function getRandomCompliment(lang = 'en') {
  const list = COMPLIMENTS[lang] || COMPLIMENTS['en'];
  let idx;
  do {
    idx = Math.floor(Math.random() * list.length);
  } while (idx === lastIndex && list.length > 1);
  lastIndex = idx;
  return list[idx];
}
