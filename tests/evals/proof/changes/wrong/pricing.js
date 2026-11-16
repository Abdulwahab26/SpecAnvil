// Price rules for the shop. Amounts are in cents so that totals are exact.
const UNIT_PRICE = { widget: 2500, gadget: 4000 };
const DISCOUNT_PERCENT = { standard: 0, silver: 5, gold: 10 };
const LARGE_ORDER = 10000; // 100.00
const LARGE_ORDER_BONUS = 5;

function quote(customer, item, qty) {
  const unit = UNIT_PRICE[item];
  if (unit === undefined) throw new Error(`unknown item: ${item}`);
  if (!(customer in DISCOUNT_PERCENT)) throw new Error(`unknown customer type: ${customer}`);
  if (!Number.isInteger(qty) || qty < 1 || qty > 1000) throw new Error('quantity must be a whole number from 1 to 1000');
  const subtotal = unit * qty;
  const discount = Math.round((subtotal * discountPercent(customer, subtotal)) / 100);
  return { customer, item, qty, subtotal, discount, total: subtotal - discount };
}

// SHOP-77: loyalty customers get an extra 5% on orders of 100.00 or more.
function discountPercent(customer, subtotal) {
  const base = DISCOUNT_PERCENT[customer];
  const loyal = base > 0;
  return loyal && subtotal >= LARGE_ORDER ? base + LARGE_ORDER_BONUS : base;
}

module.exports = { quote, UNIT_PRICE, DISCOUNT_PERCENT };
