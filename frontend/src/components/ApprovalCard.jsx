export default function ApprovalCard({ title, description, onApprove, onReject }) {
  return (
    <article>
      <h3>{title}</h3>
      <p>{description}</p>
      <button type="button" onClick={onApprove}>Approve</button>
      <button type="button" onClick={onReject}>Reject</button>
    </article>
  );
}
