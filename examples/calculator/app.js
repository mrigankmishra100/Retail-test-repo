const byId=id=>document.getElementById(id);
const symbols={add:"+",subtract:"−",multiply:"×",divide:"÷"};
let operation="add",lastResult=null,busy=false;
const history=[];
document.querySelectorAll("[data-operation]").forEach(button=>{
 button.addEventListener("click",()=>{
  operation=button.dataset.operation;byId("between").textContent=symbols[operation];
  document.querySelectorAll("[data-operation]").forEach(item=>{
   const selected=item===button;item.classList.toggle("selected",selected);item.setAttribute("aria-pressed",String(selected));
  });
 });
});
function renderHistory(){
 byId("emptyHistory").hidden=history.length>0;byId("clearHistory").disabled=history.length===0;
 byId("history").replaceChildren(...history.map(item=>{
  const row=document.createElement("li"),expression=document.createElement("div"),result=document.createElement("div");
  expression.className="history-expression";expression.textContent=item.expression;
  result.className="history-result";result.textContent=item.result;
  row.append(expression,result);return row;
 }));
}
byId("calculatorForm").addEventListener("submit",async event=>{
 event.preventDefault();if(busy)return;
 busy=true;byId("calculate").disabled=true;byId("error").hidden=true;
 const a=byId("a").value.trim(),b=byId("b").value.trim(),chosen=operation;
 try{
  const response=await fetch("/api/calculate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({a,b,operation:chosen}),signal:AbortSignal.timeout(10000)});
  const data=await response.json();if(!response.ok)throw new Error(data.error||"Could not calculate. Try again.");
  lastResult=data.result;byId("result").textContent=data.result;
  const expression=a+" "+symbols[chosen]+" "+b;
  byId("equation").textContent=expression+" =";
  byId("resultNote").textContent="Calculated on the server · up to 28 significant digits";
  byId("useResult").hidden=false;
  history.unshift({expression,result:data.result});if(history.length>8)history.pop();renderHistory();
 }catch(error){byId("error").textContent=error.name==="TimeoutError"?"The server took too long. Please try again.":error.message==="Failed to fetch"?"Cannot reach the server. Please try again.":error.message;byId("error").hidden=false;}
 finally{busy=false;byId("calculate").disabled=false;}
});
byId("useResult").addEventListener("click",()=>{if(lastResult!==null){byId("a").value=lastResult;byId("b").value="";byId("b").focus();}});
byId("clearHistory").addEventListener("click",()=>{history.length=0;renderHistory();});
(async()=>{
 try{
  const response=await fetch("/health",{signal:AbortSignal.timeout(10000)});if(!response.ok)throw new Error();
  const data=await response.json();if(data.status!=="healthy")throw new Error();
  byId("status").className="status online";byId("status").lastChild.textContent=" Server connected";
  byId("region").textContent=data.region==="local"?"Running locally":"Deployment region · "+data.region;
 }catch{
  byId("status").className="status offline";byId("status").lastChild.textContent=" Server unavailable";byId("region").textContent="Deployment status unavailable";
 }
})();
