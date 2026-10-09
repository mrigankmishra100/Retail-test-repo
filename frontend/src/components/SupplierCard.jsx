export default function SupplierCard({ supplier, price, MOQ, leadTime }) {
  return (
    <article>
      <h3>{supplier}</h3>
      <p>Price: {price}</p>
      <p>Minimum order quantity: {MOQ}</p>
      <p>Lead time: {leadTime}</p>
    </article>
  );
}
