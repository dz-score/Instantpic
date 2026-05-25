/**
 * Random compliment shown during photo reveal.
 * Keeps the emotional peak warm and personal.
 */
const COMPLIMENTS = [
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
];

let lastIndex = -1;

export function getRandomCompliment() {
  let idx;
  do {
    idx = Math.floor(Math.random() * COMPLIMENTS.length);
  } while (idx === lastIndex && COMPLIMENTS.length > 1);
  lastIndex = idx;
  return COMPLIMENTS[idx];
}
