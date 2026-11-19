import {contracts} from '../../agent/tools.mjs';

// This gateway accepts primitive function parameters but rejects the nested intent schema.
// Only the wire encoding changes: the original complete contract still validates the decoded value.
export function wireParameters(name){
  const parameters=structuredClone(contracts.tools[name].parameters);
  if(!['bio_plan','bio_continue'].includes(name))return parameters;
  const intent=parameters.properties.intent;delete parameters.properties.intent;
  parameters.properties.intentJson={type:'string',minLength:2,maxLength:65536,
    description:'A complete JSON object obeying this exact biological intent contract: '+JSON.stringify(intent)};
  parameters.required=parameters.required.map(key=>key==='intent'?'intentJson':key);
  return parameters;
}

export function decodeWireArguments(name,args){
  if(!['bio_plan','bio_continue'].includes(name))return args;
  let intent;
  try{intent=JSON.parse(args.intentJson);}catch{throw new Error('BIO_INTENT_JSON_INVALID');}
  const {intentJson,...rest}=args;return {...rest,intent};
}
