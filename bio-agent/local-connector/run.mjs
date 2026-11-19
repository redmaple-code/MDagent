import {loadConnectorConfig} from './config.mjs';import {createConnectorServer} from './server.mjs';import {createInterface} from 'node:readline';
if(process.argv.length!==4||process.argv[2]!=='--config'||!process.stdin.isTTY||!process.stdout.isTTY){process.stderr.write('Use an interactive terminal: node run.mjs --config /absolute/connector.json\n');process.exitCode=1;}
else {
  try{const config=loadConnectorConfig(process.argv[3]),server=createConnectorServer({config});await server.listen();
    const printCode=()=>process.stdout.write('Pair in your DSH page (valid for 120 seconds): '+server.pairing.issueCode()+'\n');
    process.stdout.write('Local preview connector listening on 127.0.0.1:'+server.address.port+'. Press Enter for a new pairing code.\n');printCode();
    const terminal=createInterface({input:process.stdin});terminal.on('line',printCode);let stopping=false;
    const stop=async()=>{if(stopping)return;stopping=true;terminal.close();await server.close();};process.once('SIGINT',stop);process.once('SIGTERM',stop);terminal.once('close',()=>{void stop();});
  }catch(error){process.stderr.write((/^[A-Z_]+$/.test(error.message)?error.message:'CONNECTOR_UNAVAILABLE')+'\n');process.exitCode=1;}
}
