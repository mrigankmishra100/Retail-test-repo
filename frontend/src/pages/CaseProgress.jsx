import { Badge, Icon, Status, human } from '../ui';

export function EmailSnapshot({ email, title, approved = false, rejected = false }) {
  if (!email) return null;
  const shared = email.delivery_mode === 'shared_poc_topic';
  const updateOnly = email.delivery_mode === 'case_update_topic';
  return (
    <section className="email-review-region" aria-label={title}>
      <div className="email-snapshot-heading">
        <span className="email-icon">
          <Icon name="file" size={18} />
        </span>
        <div>
          <h3>{title}</h3>
          <p>
            {updateOnly
              ? 'Response terms retained with the case.'
              : 'Review this saved message before approving.'}
          </p>
        </div>
        <Badge tone={rejected ? 'danger' : approved ? 'success' : 'warning'}>
          {rejected ? 'Rejected' : approved ? 'Human approved' : 'Approval required'}
        </Badge>
      </div>
      {shared && (
        <div className="broadcast-notice" role="note">
          <strong>Shared demo broadcast</strong>
          <p>
            All confirmed email subscribers on the shared OCI topic receive these terms. The
            supplier contact is not the only recipient.
          </p>
        </div>
      )}
      {updateOnly && (
        <p className="broadcast-notice">
          Dispatch sends a case-update notification; it does not email this full response body.
        </p>
      )}
      <div className="email-snapshot">
        <dl>
          <div>
            <dt>Audience</dt>
            <dd>
              {shared
                ? 'All confirmed subscribers on the shared demo topic'
                : updateOnly
                  ? 'Configured case-update topic'
                  : 'Dedicated supplier email subscription'}
            </dd>
          </div>
          {email.recipient && (
            <div>
              <dt>{shared ? 'Contact' : 'To'}</dt>
              <dd>{email.recipient}</dd>
            </div>
          )}
          <div>
            <dt>Subject</dt>
            <dd>{email.subject}</dd>
          </div>
        </dl>
        <pre aria-label="Email body">{email.body}</pre>
      </div>
    </section>
  );
}

export function CaseProgress({ record }) {
  const request = record.notifications?.find((n) => n.type === 'SUPPLIER_REQUEST');
  const response = record.notifications?.find((n) => n.type === 'SUPPLIER_RESPONSE');
  const late = [
    'AWAITING_SUPPLIER_APPROVAL',
    'RESPONSE_QUEUED',
    'SUPPLIER_RESPONDED',
    'SUPPLIER_REJECTED',
    'COMPLETED',
  ].includes(record.status);
  const managerApproved = late || record.status === 'REQUEST_QUEUED';
  const supplierApproved = ['RESPONSE_QUEUED', 'SUPPLIER_RESPONDED', 'COMPLETED'].includes(
    record.status,
  );
  const steps = [
    [
      'Retail review',
      record.status === 'MANAGER_REJECTED'
        ? 'REJECTED'
        : managerApproved
          ? 'APPROVED'
          : 'AWAITING_APPROVAL',
    ],
    ['Request notification', request?.status || (late ? 'SENT' : 'PENDING')],
    [
      'Supplier review',
      record.status === 'SUPPLIER_REJECTED'
        ? 'REJECTED'
        : supplierApproved
          ? 'APPROVED'
          : late
            ? 'AWAITING_APPROVAL'
            : 'WAITING',
    ],
    [
      'Response notification',
      response?.status ||
        (['SUPPLIER_RESPONDED', 'COMPLETED'].includes(record.status) ? 'SENT' : 'PENDING'),
    ],
    ['Approved summary', record.status === 'COMPLETED' ? 'COMPLETED' : 'WAITING'],
  ];
  return (
    <section className="case-progress" aria-label="Shared request progress">
      <h3>Retail & supplier progress</h3>
      <p className="muted">
        Updates from the shared case refresh automatically while this page is visible.
      </p>
      <ol>
        {steps.map(([label, state]) => (
          <li key={label}>
            <span>{label}</span>
            <Status value={state} />
          </li>
        ))}
      </ol>
      <small>
        Sent means OCI accepted the notification for publication; it does not confirm inbox delivery
        or physical fulfillment.
      </small>
      {record.decisions?.length > 0 && (
        <div className="decision-history">
          <h3>Human decisions</h3>
          {record.decisions.map((d, i) => (
            <article key={`${d.role}:${i}`}>
              <strong>{human(d.role)}</strong>{' '}
              <Status value={d.decision === 'APPROVE' ? 'APPROVED' : 'REJECTED'} />
              {d.comment && <p>{d.comment}</p>}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

export function ApprovedSummary({ record }) {
  if (record.status !== 'COMPLETED') return null;
  return (
    <section className="callout info">
      <div>
          <h3>Approved request summary</h3>
        <p>
          The approved terms for {record.item_id}, supplier {record.selected_supplier_id}, and{' '}
          {record.recommended_quantity} units are retained with this completed case.
        </p>
        <p>
          Completion records the approval and notification workflow. It is not a purchase order or
          proof of goods delivery.
        </p>
      </div>
    </section>
  );
}
