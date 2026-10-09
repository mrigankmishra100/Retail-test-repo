import Chat from "../components/Chat.jsx";

export default function RetailManager({ onLogout }) {
  return (
    <main>
      <button type="button" onClick={onLogout}>Change role</button>
      <h1>Retail Inventory Manager</h1>
      <Chat role="retail-manager" />
      {/* TODO: Display inventory risk. */}
      {/* TODO: Display supplier recommendations. */}
      {/* TODO: Display approval cards. */}
      {/* TODO: Display the active replenishment case. */}
    </main>
  );
}
