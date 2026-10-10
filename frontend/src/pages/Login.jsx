export default function Login({ onSelectRole }) {
  return (
    <main>
      <h1>Retail Inventory Agent POC</h1>
      <p>Select a role to enter the demonstration.</p>
      {/* TODO: Replace role selection with real authentication and authorization. */}
      <button type="button" onClick={() => onSelectRole("retail-manager")}>
        Retail Manager
      </button>
      <button type="button" onClick={() => onSelectRole("supplier")}>
        Supplier
      </button>
      {/* TODO: Add authenticated supplier selection for SUP001-SUP004. */}
    </main>
  );
}
