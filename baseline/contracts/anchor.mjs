import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import ganache from 'ganache';
import solc from 'solc';
import {BrowserProvider, ContractFactory} from 'ethers';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const manifestPath = path.resolve(process.argv[2] || path.join(root,'results/current_demo/manifest.json'));
const outputPath = path.resolve(process.argv[3] || path.join(fs.mkdtempSync(path.join(tmpdir(),'5017-anchor-')), 'anchor.json'));
const hash = data => crypto.createHash('sha256').update(data).digest('hex');
const canonical = obj => Array.isArray(obj) ? '['+obj.map(canonical).join(',')+']' :
  obj && typeof obj==='object' ? '{'+Object.keys(obj).sort().map(k=>JSON.stringify(k)+':'+canonical(obj[k])).join(',')+'}' : JSON.stringify(obj);

function requireNewOutput() {
  if (fs.lstatSync(outputPath, {throwIfNoEntry: false})) {
    throw new Error(`Output already exists. Choose a new receipt path: ${outputPath}`);
  }
}

async function main() {
  requireNewOutput();
  const source = fs.readFileSync(path.join(root,'contracts/EvidenceRegistry.sol'),'utf8');
  const input = {language:'Solidity',sources:{'EvidenceRegistry.sol':{content:source}},settings:{optimizer:{enabled:true,runs:200},evmVersion:'paris',outputSelection:{'*':{'*':['abi','evm.bytecode.object']}}}};
  const compiled=JSON.parse(solc.compile(JSON.stringify(input)));
  const errors=(compiled.errors||[]).filter(e=>e.severity==='error');
  if(errors.length) throw new Error(errors.map(e=>e.formattedMessage).join('\n'));
  const artifact=compiled.contracts['EvidenceRegistry.sol'].EvidenceRegistry;
  const raw=fs.readFileSync(manifestPath);
  const manifest=JSON.parse(raw);
  if(raw.toString('utf8')!==canonical(manifest)) throw new Error('Manifest must use canonical bytes without trailing newline');
  const packageHash=hash(raw);
  const engine=ganache.provider({chain:{chainId:1337},logging:{quiet:true},wallet:{deterministic:true,totalAccounts:2}});
  try {
    const provider=new BrowserProvider(engine);
    const signer=await provider.getSigner();
    const contract=await new ContractFactory(artifact.abi,'0x'+artifact.evm.bytecode.object,signer).deploy();
    await contract.waitForDeployment();
    const receipt=await (await contract.register('0x'+packageHash,'urn:sha256:'+packageHash)).wait();
    const exists=await contract.exists('0x'+packageHash);
    const record=await contract.lookup('0x'+packageHash);
    const events=receipt.logs.map(log=>{try{return contract.interface.parseLog(log)}catch{return null}}).filter(Boolean);
    const registered=events.find(event=>event.name==='EvidenceRegistered');
    const registeredHash=registered?.args.packageHash.slice(2);
    let duplicateRejected=false;
    try {await (await contract.register('0x'+packageHash,'duplicate')).wait()} catch {duplicateRejected=true}
    const tampered={...manifest,tamper_demo:'changed after registration'};
    const tamperedHash=hash(Buffer.from(canonical(tampered)));
    const tamperedRegistered=await contract.exists('0x'+tamperedHash);
    const result={tampered_package_hash:tamperedHash,tampered_registered:tamperedRegistered,tamper_detected:tamperedHash!==packageHash && !tamperedRegistered,schema_version:'2.1',chain_kind:'local_ephemeral',chain_id:1337,
      package_hash:packageHash,registered_hash:registeredHash,
      read_back_hash:exists && record.uri==='urn:sha256:'+packageHash ? packageHash : null,
      contract_address:await contract.getAddress(),transaction_hash:receipt.hash,
      block_hash:receipt.blockHash,block_number:receipt.blockNumber,submitter:record.submitter,
      timestamp:Number(record.timestamp),duplicate_rejected:duplicateRejected,compiler:solc.version(),
      source_sha256:hash(source),bytecode_sha256:hash(Buffer.from(artifact.evm.bytecode.object,'hex')),
      trust_limit:'Saved local in-memory chain record; no public-chain timestamp or data-truth guarantee',
      status:exists && registeredHash===packageHash && duplicateRejected && tamperedHash!==packageHash && !tamperedRegistered ? 'PASS':'FAIL'};
    fs.mkdirSync(path.dirname(outputPath),{recursive:true});
    const temporaryDir = fs.mkdtempSync(path.join(path.dirname(outputPath), '.anchor-'));
    try {
      const temporaryReceipt = path.join(temporaryDir, 'receipt.json');
      fs.writeFileSync(temporaryReceipt, JSON.stringify(result, null, 2) + '\n', {flag: 'wx'});
      fs.linkSync(temporaryReceipt, outputPath);
    } finally {
      fs.rmSync(temporaryDir, {recursive: true, force: true});
    }
    console.log(JSON.stringify({status:result.status,package_hash:packageHash,duplicate_rejected:duplicateRejected,output:outputPath}));
    if(result.status!=='PASS') process.exitCode=1;
  } finally {await engine.disconnect()}
}
main().catch(error=>{console.error(error.message);process.exitCode=1});
