import Chat from "../components/Chat.jsx";

export default function Supplier({ onLogout }) {
  return (
    <main>
      <button type="button" onClick={onLogout}>Change role</button>
      <h1>Supplier Operations</h1>
      <Chat role="supplier" />
      {/* TODO: Display the incoming replenishment request. */}
      {/* TODO: Display supplier inventory. */}
      {/* TODO: Display the prepared quotation. */}
      {/* TODO: Display the supplier approval action. */}
    </main>
  );
}
