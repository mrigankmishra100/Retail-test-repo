export default function InventoryCard({ item, quantity, daysOfSupply, risk }) {
  return (
    <article>
      <h3>{item}</h3>
      <p>Quantity: {quantity}</p>
      <p>Days of supply: {daysOfSupply}</p>
      <p>Risk: {risk}</p>
    </article>
  );
}
